#!/usr/bin/env python3
"""Capture Preview.png — run from repo root: python scripts/capture_preview.py"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

import duplicate_finder as df  # noqa: E402


def _window_bbox(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    return rect.left, rect.top, rect.right, rect.bottom


def main() -> None:
    # Fixed size for a consistent README screenshot (not maximized).
    df.DuplicateFinderApp._maximize_window = lambda self: self.geometry("1100x800")  # type: ignore[method-assign]

    app = df.DuplicateFinderApp()
    app._path_var.set("D:/Music/")
    app._check_dir_var.set("D:/Music/")
    app._mode_var.set("name_similar")
    app._recursive_var.set(True)
    app._check_dir_subfolders_var.set(True)
    app._status_var.set(
        "Found 0 duplicate group(s), 0 file(s). Recoverable space: ~0 B."
    )
    app._refresh_tree()
    app.update_idletasks()
    app.update()
    time.sleep(0.6)

    hwnd = int(app.winfo_id())
    bbox = _window_bbox(hwnd)
    img = ImageGrab.grab(bbox=bbox)
    out = ROOT / "Preview.png"
    img.save(out, format="PNG", optimize=True)
    print(f"Saved {out} ({img.size[0]}x{img.size[1]})")
    app.destroy()


if __name__ == "__main__":
    main()
