"""Load updated code cleanly after a deploy, without restarting the server.

Streamlit Cloud updates the files in place when a commit lands and reruns
app.py, but modules imported by the old code can stay in ``sys.modules``. Twice
in November 2026 that left the app running new app.py against an old
data/eia.py (an ImportError) or serving results cached by the old code.

On every run app.py calls :func:`purge_if_changed` before importing anything
from the project. It fingerprints the project's .py files by path, size and
modification time (a few milliseconds). When the fingerprint differs from the
previous run, it removes every project module from ``sys.modules`` so the next
imports load the new files, and returns True so the caller can clear caches.

Side effects of a deploy, by design: in-memory telemetry and the public
Committee run counter start again, as they would after a restart.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_STATE_ATTR = "_eis_code_fingerprint"


def fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    """Path, size and mtime of every .py file under *root*."""
    items = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(items))


def _project_modules(root: Path) -> list[str]:
    prefix = str(root.resolve()) + os.sep
    names = []
    for name, module in list(sys.modules.items()):
        if name in ("__main__", __name__):
            continue
        file = getattr(module, "__file__", None)
        if file and str(Path(file).resolve()).startswith(prefix):
            names.append(name)
    return names


def purge_if_changed(root: Path) -> bool:
    """Drop project modules from sys.modules if any source file changed.

    Returns True when code changed since the previous call in this process.
    The first call only records the fingerprint and returns False.
    """
    current = fingerprint(root)
    previous = getattr(sys, _STATE_ATTR, None)
    setattr(sys, _STATE_ATTR, current)
    if previous is None or previous == current:
        return False
    for name in _project_modules(root):
        sys.modules.pop(name, None)
    return True
