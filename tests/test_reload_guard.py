"""A deploy that changes files reloads project modules instead of keeping old ones."""
from __future__ import annotations

import importlib
import os
import sys

import reload_guard


def test_changed_file_is_reloaded(tmp_path, monkeypatch):
    pkg = tmp_path / "proj"
    (pkg / "data").mkdir(parents=True)
    (pkg / "data" / "__init__.py").write_text("")
    mod = pkg / "data" / "regions.py"
    mod.write_text("OLD = 1\n")
    monkeypatch.syspath_prepend(str(pkg))
    monkeypatch.delattr(sys, reload_guard._STATE_ATTR, raising=False)
    for name in ("data", "data.regions"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    import data.regions as first
    assert first.OLD == 1
    assert reload_guard.purge_if_changed(pkg) is False      # first run records only
    assert reload_guard.purge_if_changed(pkg) is False      # nothing changed

    mod.write_text("OLD = 1\nNEW = 2\n")
    stat = mod.stat()
    os.utime(mod, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert reload_guard.purge_if_changed(pkg) is True
    assert "data.regions" not in sys.modules and "data" not in sys.modules
    importlib.invalidate_caches()
    from data.regions import NEW                              # would be ImportError before
    assert NEW == 2


def test_modules_outside_the_project_are_kept(tmp_path, monkeypatch):
    pkg = tmp_path / "proj"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.delattr(sys, reload_guard._STATE_ATTR, raising=False)
    reload_guard.purge_if_changed(pkg)
    (pkg / "b.py").write_text("y = 1\n")
    assert reload_guard.purge_if_changed(pkg) is True
    assert "os" in sys.modules and "importlib" in sys.modules and "reload_guard" in sys.modules
