import io

p = "tradesome/app/services/market_service.py"
s = io.open(p, encoding="utf-8").read()

old = '''    def install_akshare(self, timeout: int = 300) -> dict:'''
new = '''    def install_akshare(self, timeout: int = 900) -> dict:'''
assert old in s
s = s.replace(old, new, 1)

old = '''    def ensure_akshare(self, timeout: int = 300) -> dict:'''
new = '''    def ensure_akshare(self, timeout: int = 900) -> dict:'''
assert old in s
s = s.replace(old, new, 1)

old = '''        except subprocess.TimeoutExpired:
            return {"ok": False, "returncode": None,
                    "message": "自动安装超时（%d 秒）。请检查网络后重试。" % timeout,
                    "output": ""}'''
new = '''        except subprocess.TimeoutExpired as exc:
            partial = ""
            for chunk in (getattr(exc, "stdout", None), getattr(exc, "stderr", None)):
                if not chunk:
                    continue
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", "replace")
                partial += chunk
            return {"ok": False, "returncode": None,
                    "message": ("自动安装超时（%d 秒）。akshare 与依赖较大，"
                                "网络慢时可能超过 5 分钟；请保持网络后重试，"
                                "或把完整 wheel 放进 vendor/akshare/。" % timeout),
                    "output": partial[-4000:]}'''
assert old in s
s = s.replace(old, new, 1)

old = '''        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input"]'''
new = '''        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off"]'''
assert old in s
s = s.replace(old, new, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
