# 离线安装 akshare（可选）

软件首次启动会自动执行：

```
python -m pip install akshare>=1.14
```

需要联网。若目标机器不能联网，可提前在一台可联网、**同为 Windows + Python 3.9** 的机器上下载 wheel：

```bat
python -m pip download akshare -d vendor\akshare --only-binary=:all: --python-version 3.9
```

把下载到的 `*.whl`（包括依赖 wheel）连同本说明一起放到本目录，启动软件时会自动改用：

```
python -m pip install --no-index --find-links vendor\akshare akshare>=1.14
```

注意：

- 只放 `akshare` 本身不够，`pandas`、`requests`、`beautifulsoup4` 等依赖也需要 wheel；`pip download` 会一并下载。
- wheel 必须与目标机器的 Python 版本、位数匹配（本软件要求 64 位 Python 3.9+）。
- 这里的内容只影响「同步真实行情」工具，不影响软件本体启动与回测。
