"""CLI 入口：python -m server.cli ingest/ask/run/eval/doc-list/doc-delete。

逻辑全部在 server.core.main；本文件只是正规的模块入口。
"""
from __future__ import annotations

from server.core.main import main


if __name__ == "__main__":
    main()
