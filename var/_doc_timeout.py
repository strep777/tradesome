import io

p = "docs/17-真实行情接入设计.md"
s = io.open(p, encoding="utf-8").read()
old = "| `install_akshare(timeout=300)` | `{ok, returncode, message, output, wheels}` | 执行固定命令 `python -m pip install akshare>=1.14`；有 `vendor/akshare/*.whl` 时改用 `--no-index` |"
new = "| `install_akshare(timeout=900)` | `{ok, returncode, message, output, wheels, skipped_wheels}` | 执行固定命令 `python -m pip install akshare>=1.14`；有 vendor wheel 时只加为 `--find-links`，并跳过与当前 Python 不兼容的 wheel |"
assert old in s
s = s.replace(old, new, 1)
s = s.replace("| `ensure_akshare(timeout=300)` |", "| `ensure_akshare(timeout=900)` |", 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
