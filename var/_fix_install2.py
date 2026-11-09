import io

p = "tradesome/app/services/market_service.py"
s = io.open(p, encoding="utf-8").read()

old_cmd = '''        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off"]
        if wheels:
            # 只把本地目录作为额外的候选来源，不屏蔽在线索引：
            # 缺依赖时 pip 仍可联网补齐，不会被一个 wheel 卡死。
            cmd += ["--find-links", str(self._vendor_dir)]
        cmd.append("akshare>=1.14")
        return cmd'''
new_cmd = '''        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off", "--timeout", "20", "--retries", "1"]
        if wheels:
            # 只把本地目录作为额外的候选来源，不屏蔽在线索引：
            # 缺依赖时 pip 仍可联网补齐，不会被一个 wheel 卡死。
            cmd += ["--find-links", str(self._vendor_dir)]
        cmd.append("akshare>=1.14")
        return cmd

    @staticmethod
    def _pip_env(direct: bool) -> dict:
        """直连时清掉代理；代理模式保留系统设置。"""
        env = dict(os.environ)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        if direct:
            for key in list(env.keys()):
                if key.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                    env.pop(key, None)
            env["NO_PROXY"] = "*"
            env["no_proxy"] = "*"
        return env'''
assert old_cmd in s
s = s.replace(old_cmd, new_cmd, 1)

start = s.index('''        cmd = self._install_command(wheels)''')
end = s.index('''    def ensure_akshare(''')
new_body = '''        cmd = self._install_command(wheels)
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        attempts = [(True, min(timeout, 300)), (False, timeout)]
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
                                        "pip 退出码 %s" % proc.returncode))
        if ok:
            message = "akshare 安装完成。"
        else:
            message = "自动安装失败：%s" % ("；".join(failures)[:260] or "未知原因")
            if skipped:
                message += "。vendor 中 %d 个 wheel 与 Python %d.%d 不兼容，已跳过" % (
                    len(skipped), sys.version_info[0], sys.version_info[1])
        try:
            self.c.repos.audit.write(
                "data", "akshare_install",
                detail="%s，可用包 %d 个，跳过 %d 个，尝试 %d 次" % (
                    "成功" if ok else "失败", len(wheels), len(skipped),
                    len(failures) + (1 if ok else 0)))
        except Exception:  # noqa: BLE001 - 审计失败不影响安装结果
            pass
        LOGGER.info("akshare 自动安装：ok=%s wheels=%d skipped=%d rc=%s attempts=%d",
                    ok, len(wheels), len(skipped), returncode, len(failures) + (1 if ok else 0))
        return {"ok": ok, "returncode": returncode, "message": message,
                "output": output[-4000:], "wheels": len(wheels),
                "skipped_wheels": len(skipped)}

    @staticmethod
    def _timeout_output(exc) -> str:
        partial = ""
        for chunk in (getattr(exc, "stdout", None), getattr(exc, "stderr", None)):
            if not chunk:
                continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", "replace")
            partial += chunk
        return partial

'''
s = s[:start] + new_body + s[end:]
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
