"""Test configuration: run offline, against bundled fixtures, on a temp database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

os.environ["OFFLINE"] = "1"
os.environ["CACHE_DIR"] = str(FIXTURES / "cache")
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="podx-test-")) / "podx.sqlite")
os.environ["ENABLE_WATCHER"] = "0"

import podx.config as config  # noqa: E402

config._settings = None  # drop any settings captured before the env above was set
