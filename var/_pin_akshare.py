import io

p = "tradesome/app/services/market_service.py"
s = io.open(p, encoding="utf-8").read()
s = s.replace('        cmd.append("akshare>=1.14")',
              '        cmd.append(AKSHARE_REQUIREMENT)', 1)
old = 'LOGGER = get_logger("app.market")\n'
new = ('LOGGER = get_logger("app.market")\n\n'
       '# 固定版本避免 pip 在几百个 akshare 版本间回溯；该版本支持 Python >=3.9。\n'
       'AKSHARE_REQUIREMENT = "akshare==1.18.88"\n')
assert old in s
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)

p = "tests/unit/test_akshare.py"
s = io.open(p, encoding="utf-8").read()
s = s.replace('self.assertIn("akshare>=1.14", cmd)',
              'self.assertIn("akshare==1.18.88", cmd)', 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)

p = "docs/17-真实行情接入设计.md"
s = io.open(p, encoding="utf-8").read()
s = s.replace("`python -m pip install akshare>=1.14`",
              "`python -m pip install akshare==1.18.88`")
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
