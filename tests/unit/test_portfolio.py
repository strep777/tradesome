"""账户、持仓与交易成本测试。"""

from __future__ import annotations

import unittest
from datetime import datetime

from tradesome.backtest.costs import (CostBreakdown, apply_slippage, buy_cost,
                                      limit_fill_price, sell_cost,
                                      total_fees)
from tradesome.core.errors import ExecutionError, ValidationError
from tradesome.core.types import (SIDE_BUY, SIDE_SELL, FeeProfile, Fill,
                                  Position)
from tradesome.portfolio import Account

DT = datetime(2024, 3, 1, 15, 0)


def make_fill(order_id="O-1", symbol="600519.SH", side=SIDE_BUY, qty=100,
              price=10.0, commission=5.0, stamp=0.0, transfer=0.0) -> Fill:
    return Fill(fill_id="F-" + order_id, order_id=order_id, symbol=symbol,
                side=side, qty=qty, price=price, dt=DT,
                commission=commission, stamp_tax=stamp, transfer_fee=transfer)


class CostTest(unittest.TestCase):
    def test_buy_cost_uses_min_commission(self) -> None:
        fee = FeeProfile(commission_rate=0.00025, min_commission=5.0,
                         transfer_fee_rate=0.00001)
        commission, transfer, total = buy_cost(1000.0, fee)
        self.assertEqual(commission, 5.0)
        self.assertEqual(transfer, 0.01)
        self.assertEqual(total, 5.01)

    def test_buy_cost_large_amount(self) -> None:
        fee = FeeProfile(commission_rate=0.00025, min_commission=5.0,
                         transfer_fee_rate=0.00001)
        commission, transfer, total = buy_cost(1_000_000.0, fee)
        self.assertEqual(commission, 250.0)
        self.assertEqual(transfer, 10.0)

    def test_sell_cost_includes_stamp_tax_only_on_sell(self) -> None:
        fee = FeeProfile(commission_rate=0.00025, min_commission=5.0,
                         stamp_tax_rate=0.0005, transfer_fee_rate=0.00001)
        total, stamp, commission = sell_cost(100_000.0, fee)
        self.assertEqual(stamp, 50.0)
        self.assertEqual(commission, 25.0)
        self.assertEqual(total, 76.0)
        self.assertEqual(buy_cost(100_000.0, fee)[2], 26.0)

    def test_total_fees_dispatch(self) -> None:
        fee = FeeProfile()
        self.assertEqual(total_fees(SIDE_BUY, 10_000.0, fee),
                         buy_cost(10_000.0, fee)[2])
        self.assertEqual(total_fees(SIDE_SELL, 10_000.0, fee),
                         sell_cost(10_000.0, fee)[0])

    def test_slippage_direction_and_tick(self) -> None:
        fee = FeeProfile(slippage_bps=100.0)   # 1%
        self.assertEqual(apply_slippage(10.0, SIDE_BUY, fee), 10.1)
        self.assertEqual(apply_slippage(10.0, SIDE_SELL, fee), 9.9)
        self.assertEqual(apply_slippage(10.005, SIDE_BUY, fee), 10.11)
        self.assertEqual(apply_slippage(10.005, SIDE_SELL, fee), 9.9)

    def test_limit_fill_price(self) -> None:
        self.assertEqual(limit_fill_price(9.5, 10.0, SIDE_BUY), 9.5)
        self.assertEqual(limit_fill_price(10.5, 10.0, SIDE_BUY), 10.0)
        self.assertEqual(limit_fill_price(10.5, 10.0, SIDE_SELL), 10.5)
        self.assertEqual(limit_fill_price(9.5, 10.0, SIDE_SELL), 10.0)

    def test_cost_breakdown_total(self) -> None:
        breakdown = CostBreakdown(commission=5.0, stamp_tax=10.0,
                                  transfer_fee=0.2)
        self.assertEqual(breakdown.total, 15.2)


class AccountTest(unittest.TestCase):
    def test_rejects_invalid_cash(self) -> None:
        with self.assertRaises(ValidationError):
            Account(0)
        with self.assertRaises(ValidationError):
            Account(-100)

    def test_initial_state(self) -> None:
        account = Account(100_000)
        self.assertEqual(account.cash, 100_000)
        self.assertEqual(account.equity, 100_000)
        self.assertEqual(account.position_value, 0.0)
        self.assertEqual(account.buying_power, 100_000)
        self.assertEqual(account.position_list, [])

    def test_freeze_and_release(self) -> None:
        account = Account(10_000)
        self.assertTrue(account.freeze("O-1", 3_000))
        self.assertEqual(account.cash, 7_000)
        self.assertEqual(account.frozen_cash, 3_000)
        self.assertEqual(account.equity, 10_000)
        self.assertEqual(account.frozen_for("O-1"), 3_000)
        self.assertFalse(account.freeze("O-2", 8_000))
        self.assertEqual(account.release("O-1"), 3_000)
        self.assertEqual(account.cash, 10_000)
        self.assertEqual(account.release("O-1"), 0.0)

    def test_buy_fill_creates_position_with_t_plus_1(self) -> None:
        account = Account(100_000)
        account.apply_fill(make_fill(qty=100, price=10.0))
        position = account.position_of("600519.SH")
        self.assertIsNotNone(position)
        self.assertEqual(position.qty, 100)
        self.assertEqual(position.available_qty, 0)
        self.assertEqual(position.avg_cost, 10.05)
        self.assertEqual(account.cash, 98_995.0)
        self.assertEqual(account.position_value, 1_000.0)
        self.assertEqual(account.fees_paid, 5.0)
        account.settle_day(DT)
        self.assertEqual(account.position_of("600519.SH").available_qty, 100)

    def test_buy_fill_consumes_reservation(self) -> None:
        account = Account(100_000)
        account.freeze("O-1", 1_005.0)
        account.apply_fill(make_fill(order_id="O-1", qty=100, price=10.0))
        self.assertEqual(account.frozen_cash, 0.0)
        self.assertEqual(account.cash, 98_995.0)

    def test_buy_without_cash_raises(self) -> None:
        account = Account(1_000)
        with self.assertRaises(ExecutionError):
            account.apply_fill(make_fill(qty=100, price=10.0))

    def test_second_buy_averages_cost(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(order_id="O-1", qty=100, price=10.0))
        account.apply_fill(make_fill(order_id="O-2", qty=100, price=20.0,
                                     commission=5.0))
        position = account.position_of("600519.SH")
        self.assertEqual(position.qty, 200)
        self.assertEqual(position.available_qty, 200)
        self.assertAlmostEqual(position.avg_cost, 15.05, places=6)

    def test_sell_realizes_pnl_and_updates_cash(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(qty=100, price=10.0))
        realized = account.apply_fill(make_fill(
            order_id="O-2", side=SIDE_SELL, qty=100, price=12.0,
            commission=5.0, stamp=0.6))
        self.assertAlmostEqual(realized, 189.4, places=2)
        self.assertIsNone(account.position_of("600519.SH"))
        self.assertEqual(account.cash, 100_189.4)
        self.assertEqual(account.realized_pnl, 189.4)

    def test_partial_sell_keeps_position(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(qty=200, price=10.0))
        account.apply_fill(make_fill(order_id="O-2", side=SIDE_SELL,
                                     qty=100, price=11.0, commission=5.0))
        position = account.position_of("600519.SH")
        self.assertEqual(position.qty, 100)
        self.assertEqual(position.available_qty, 100)

    def test_sell_without_position_raises(self) -> None:
        account = Account(100_000)
        with self.assertRaises(ExecutionError):
            account.apply_fill(make_fill(side=SIDE_SELL, qty=100))

    def test_drawdown_and_peak(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(qty=100, price=10.0))
        account.mark_to_market({"600519.SH": 20.0}, DT)
        self.assertGreater(account.equity, 100_000)
        peak = account.peak_equity
        account.mark_to_market({"600519.SH": 10.0}, DT)
        self.assertEqual(account.peak_equity, peak)
        self.assertAlmostEqual(account.drawdown, 1 - account.equity / peak)

    def test_day_pnl_after_settle(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(qty=100, price=10.0))
        account.settle_day(DT)
        account.mark_to_market({"600519.SH": 11.0}, DT)
        self.assertGreater(account.day_pnl, 0)
        self.assertGreater(account.day_pnl_pct, 0)

    def test_snapshot_fields(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(qty=100, price=10.0))
        snapshot = account.snapshot(DT)
        self.assertEqual(snapshot.dt, DT)
        self.assertEqual(snapshot.cash, account.cash)
        self.assertEqual(snapshot.equity, account.equity)
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(snapshot.positions[0].symbol, "600519.SH")

    def test_reset(self) -> None:
        account = Account(100_000, t_plus=0)
        account.apply_fill(make_fill(qty=100, price=10.0))
        account.reset(50_000)
        self.assertEqual(account.cash, 50_000)
        self.assertEqual(account.position_list, [])
        self.assertEqual(account.equity, 50_000)


if __name__ == "__main__":
    unittest.main()
