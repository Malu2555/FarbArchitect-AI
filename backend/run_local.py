"""Local development entrypoint - replaces ``manage.py runserver``.

Usage::

    cd backend
    .\\.venv\\Scripts\\Activate.ps1
    python run_local.py
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    os.environ.setdefault("DJANGO_DEBUG", "true")
    # Ensure backend/ is importable when invoked from a different cwd.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "uvicorn is not installed. Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    reload_flag = os.environ.get("UVICORN_RELOAD", "0") == "1"
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8080")),
        reload=reload_flag,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
