"""Vercel entrypoint.

Vercel's FastAPI support auto-detects a root-level `index.py` and serves the
exported ASGI `app` at the site root, so this is the same object uvicorn serves
locally — one application, two front doors. The sys.path line exists because the
function is bundled from the project root and must import the local `podx`
package rather than a pip-installed copy.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from podx.web import app  # noqa: E402

__all__ = ["app"]
