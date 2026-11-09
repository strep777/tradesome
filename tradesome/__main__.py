"""允许 `python -m tradesome` 直接启动。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
