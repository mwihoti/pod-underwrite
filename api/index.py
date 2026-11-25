"""Vercel entrypoint.

Vercel's Python runtime passes an ASGI object straight to its request handler, so
this is the same `app` uvicorn serves locally — one application, two front doors.
The sys.path line exists because the function is uploaded from `api/` while the
package sits at the project root.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from podx.web import app  # noqa: E402

__all__ = ["app"]
