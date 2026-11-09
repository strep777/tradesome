import io

p = "tradesome/app/services/market_service.py"
s = io.open(p, encoding="utf-8").read()

old = '''        attempts = [(True, min(timeout, 300)), (False, timeout)]
        output = ""
        failures: List[str] = []
        ok = False
        returncode = None
        for direct, budget in attempts:
            label = "直连" if direct else "代理"
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=budget, cwd=str(root),
                                      env=self._pip_env(direct),
                                      creationflags=flags)
            except subprocess.TimeoutExpired as exc:
                output = self._timeout_output(exc)
                failures.append("%s超时（%d 秒）" % (label, budget))
                continue
            except Exception as exc:  # noqa: BLE001
                failures.append("%s失败：%s" % (label, exc))
                continue
            output = ((proc.stdout or "") + "\\n" + (proc.stderr or "")).strip()
            returncode = proc.returncode
            if proc.returncode == 0:
                ok = True
                break
            failures.append("%s：%s" % (label, self._pip_reason(output) or
                                        "pip 退出码 %s" % proc.returncode))'''

new = '''        attempts = [(True, timeout), (False, timeout)]
        output = ""
        failures: List[str] = []
        ok = False
        returncode = None
        for direct, budget in attempts:
            label = "直连" if direct else "代理"
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=budget, cwd=str(root),
                                      env=self._pip_env(direct),
                                      creationflags=flags)
            except subprocess.TimeoutExpired as exc:
                output = self._timeout_output(exc)
                failures.append("%s超时（%d 秒）" % (label, budget))
                # 直连都超时说明是下载慢，不必再用更慢的代理重来一遍
                if direct:
                    break
                continue
            except Exception as exc:  # noqa: BLE001
                failures.append("%s失败：%s" % (label, exc))
                continue
            output = ((proc.stdout or "") + "\\n" + (proc.stderr or "")).strip()
            returncode = proc.returncode
            if proc.returncode == 0:
                ok = True
                break
            failures.append("%s：%s" % (label, self._pip_reason(output) or
                                        "pip 退出码 %s" % proc.returncode))'''

assert old in s
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
