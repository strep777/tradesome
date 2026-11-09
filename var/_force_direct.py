import io

p = "tradesome/app/services/market_service.py"
s = io.open(p, encoding="utf-8").read()

old = '''    def _install_command(self, wheels: List[Path]) -> List[str]:
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off", "--timeout", "20", "--retries", "1"]
        if wheels:'''
new = '''    def _install_command(self, wheels: List[Path], direct: bool = False) -> List[str]:
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off", "--timeout", "20", "--retries", "1"]
        if direct:
            # 显式空代理：Windows 会从注册表读系统代理，仅清环境变量不够
            cmd += ["--proxy", ""]
        if wheels:'''
assert old in s
s = s.replace(old, new, 1)

old = '''        cmd = self._install_command(wheels)
        flags = 0'''
new = '''        flags = 0'''
assert old in s
s = s.replace(old, new, 1)

old = '''        for direct, budget in attempts:
            label = "直连" if direct else "代理"
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,'''
new = '''        for direct, budget in attempts:
            label = "直连" if direct else "代理"
            cmd = self._install_command(wheels, direct=direct)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,'''
assert old in s
s = s.replace(old, new, 1)

old = '''               "--progress-bar", "off", "--timeout", "20", "--retries", "1"]
        if direct:'''
# 注释位置检查：确保上面的替换生效后命令块存在
assert 'if direct:' in s

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
