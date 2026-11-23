"""Entrypoint Render. Tự thêm thư mục src, không cần PYTHONPATH trên dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shop_integration.web import serve


if __name__ == "__main__":
    serve()
