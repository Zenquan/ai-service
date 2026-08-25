"""兼容入口：等价于 `python main.py ...`（逻辑全部在 main.py）。

推荐直接用 main.py：`python main.py ingest/ask/run/eval ...`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import main  # noqa: E402


if __name__ == "__main__":
    main()