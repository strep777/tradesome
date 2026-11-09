import io

p = "tradesome/app/services/market_service.py"
s = io.open(p, encoding="utf-8").read()

old_imports = "import subprocess\nimport sys\n"
new_imports = "import subprocess\nimport sys\nimport zipfile\n"
assert old_imports in s
s = s.replace(old_imports, new_imports, 1)

start = s.index("    def install_akshare(self, timeout: int = 300) -> dict:")
end = s.index("    def ensure_akshare(self, timeout: int = 300) -> dict:")
new_block = '''    @staticmethod
    def _wheel_python_spec(wheel: Path) -> str:
        """读 wheel 的 Requires-Python；读不到时返回空串（按兼容处理）。"""
        try:
            with zipfile.ZipFile(str(wheel)) as archive:
                for name in archive.namelist():
                    if name.endswith(".dist-info/METADATA"):
                        for line in archive.read(name).decode(
                                "utf-8", "replace").splitlines():
                            if line.startswith("Requires-Python:"):
                                return line.split(":", 1)[1].strip()
        except Exception:  # noqa: BLE001 - 坏 wheel 交给 pip 报错
            return ""
        return ""

    @staticmethod
    def _version_tuple(text: str):
        parts = []
        for piece in str(text).split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts)

    @classmethod
    def _spec_allows_current_python(cls, spec: str) -> bool:
        if not spec:
            return True
        current = sys.version_info[:3]
        for clause in spec.strip().strip("()").split(","):
            clause = clause.strip()
            if not clause:
                continue
            for operator in (">=", "<=", "==", "!=", ">", "<", "~="):
                if clause.startswith(operator):
                    raw = clause[len(operator):].strip()
                    break
            else:
                continue
            parts = cls._version_tuple(raw.rstrip(".*"))
            if not parts:
                continue
            width = max(len(parts), 3)
            left = current + (0,) * (width - len(current))
            right = parts + (0,) * (width - len(parts))
            if operator == ">=" and not left >= right:
                return False
            if operator == ">" and not left > right:
                return False
            if operator == "<=" and not left <= right:
                return False
            if operator == "<" and not left < right:
                return False
            if operator == "==" and not left[:len(parts)] == right[:len(parts)]:
                return False
            if operator == "!=" and left[:len(parts)] == right[:len(parts)]:
                return False
            if operator == "~=" and not left >= right:
                return False
        return True

    def _install_command(self, wheels: List[Path]) -> List[str]:
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input"]
        if wheels:
            # 只把本地目录作为额外的候选来源，不屏蔽在线索引：
            # 缺依赖时 pip 仍可联网补齐，不会被一个 wheel 卡死。
            cmd += ["--find-links", str(self._vendor_dir)]
        cmd.append("akshare>=1.14")
        return cmd

    @staticmethod
    def _pip_reason(output: str) -> str:
        keys = ("ERROR:", "Requires-Python", "No matching distribution",
                "Could not find a version", "Could not build wheels")
        for line in reversed((output or "").splitlines()):
            text = line.strip()
            if text and any(key in text for key in keys):
                return text[:240]
        return ""

    def install_akshare(self, timeout: int = 300) -> dict:
        """自动把 akshare 装进当前 Python，必要时使用 vendor/akshare 的 wheel。

        只安装固定包名，不接受外部输入；失败返回可读原因，由界面重试。
        注意：不兼容当前 Python 的 wheel 会被跳过，避免 no-index 死锁。
        """
        root = self._tool_path.parent.parent
        vendor = self._vendor_dir
        all_wheels = sorted(vendor.glob("*.whl")) if vendor.is_dir() else []
        wheels = [w for w in all_wheels
                  if self._spec_allows_current_python(self._wheel_python_spec(w))]
        skipped = [w for w in all_wheels if w not in wheels]
        cmd = self._install_command(wheels)
        env = dict(os.environ)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout, cwd=str(root), env=env,
                                  creationflags=flags)
        except subprocess.TimeoutExpired:
            return {"ok": False, "returncode": None,
                    "message": "自动安装超时（%d 秒）。请检查网络后重试。" % timeout,
                    "output": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "returncode": None,
                    "message": "自动安装失败：%s" % exc, "output": ""}
        output = ((proc.stdout or "") + "\\n" + (proc.stderr or "")).strip()
        ok = proc.returncode == 0
        if ok:
            message = "akshare 安装完成。"
        else:
            reason = self._pip_reason(output)
            message = "自动安装失败：%s" % (reason or "pip 退出码 %s" % proc.returncode)
            if skipped:
                message += "。vendor 中 %d 个 wheel 与 Python %d.%d 不兼容，已跳过" % (
                    len(skipped), sys.version_info[0], sys.version_info[1])
        try:
            self.c.repos.audit.write(
                "data", "akshare_install",
                detail="%s，可用包 %d 个，跳过 %d 个" % (
                    "成功" if ok else "失败", len(wheels), len(skipped)))
        except Exception:  # noqa: BLE001 - 审计失败不影响安装结果
            pass
        LOGGER.info("akshare 自动安装：ok=%s wheels=%d skipped=%d rc=%s",
                    ok, len(wheels), len(skipped), proc.returncode)
        return {"ok": ok, "returncode": proc.returncode, "message": message,
                "output": output[-4000:], "wheels": len(wheels),
                "skipped_wheels": len(skipped)}

'''
s = s[:start] + new_block + s[end:]
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
