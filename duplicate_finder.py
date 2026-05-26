#!/usr/bin/env python3
"""Duplicate file finder — scan a folder, list duplicates, filter, delete."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# UTF-8 everywhere on Windows (paths, UI, filters).
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

CHUNK_SIZE = 1024 * 1024  # 1 MiB

CHK_OFF = "☐"
CHK_ON = "☑"

def _app_dir() -> Path:
    """Folder for settings: next to the .exe when frozen, else next to the script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_SETTINGS_FILE = _app_dir() / "duplicate_finder_settings.json"
_VALID_SCAN_MODES = frozenset(
    {"content", "name", "name_size", "name_similar", "size"}
)

# Fonts that render Arabic script correctly in Tk.
_ARABIC_FONTS = ("Segoe UI", "Tahoma", "Arial Unicode MS", "Arial", "Microsoft Sans Serif")


def pick_unicode_font(root: tk.Misc, size: int = 10) -> tkfont.Font:
    available = set(tkfont.families(root))
    for family in _ARABIC_FONTS:
        if family in available:
            return tkfont.Font(root=root, family=family, size=size)
    return tkfont.Font(root=root, size=size)


def norm_text(value: str) -> str:
    """Normalize Unicode for Arabic/Latin search and name compare."""
    return unicodedata.normalize("NFKC", value).casefold()


# Strip resolution tags even when glued (e.g. "Video720p", "Music Video1080p").
_RE_RESOLUTION = re.compile(r"\d{3,4}p", re.IGNORECASE)
_RE_QUALITY = re.compile(r"(?<![a-z0-9])(?:4k|8k|uhd|hd)(?![a-z0-9])", re.IGNORECASE)
# feat / ft / featuring → single token
_RE_FEAT = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\b", re.IGNORECASE)
# prod / produced by → single token (often in parentheses)
_RE_PROD = re.compile(r"\b(?:prod\.?|produced\s+by)\b", re.IGNORECASE)
_RE_PROD_PAREN = re.compile(
    r"[\(\[]\s*(?:prod\.?|produced\s+by)\s+([^)\]]*)[\)\]]",
    re.IGNORECASE,
)
# Common video/music filename clutter (order: longer phrases first).
_RE_JUNK_PHRASES = re.compile(
    r"\b(?:official\s+music\s+video|official\s+video|music\s+video|"
    r"official\s+audio|lyric\s+video|full\s+video|hd\s+video|"
    r"official|lyrics?|audio|video|omv|mv|hd|uhd)\b",
    re.IGNORECASE,
)


def normalize_media_filename(name: str) -> str:
    """
    Canonical key for messy music/video filenames.

    Turns variants like:
      MC TYSON Make It Rain feat eyden Watson Official Music Video720p.mp4
      MC TYSON " Make It Rain " feat. eyden & Watson (Official Music Video).mp4
      Awich GILA GILA feat JP THE WAVY YZERR Prod Chaki Zulu720p.mp4
      Awich - GILA GILA feat. JP THE WAVY, YZERR (Prod. Chaki Zulu).mp4
    into the same normalized string so they can be grouped as duplicates.
    """
    stem = Path(name).stem
    s = unicodedata.normalize("NFKC", stem)
    s = s.casefold()
    # Quotes and similar wrappers
    s = re.sub(r'["\'`´''""«»]', " ", s)
    s = _RE_RESOLUTION.sub(" ", s)
    s = _RE_QUALITY.sub(" ", s)
    s = _RE_FEAT.sub(" feat ", s)
    # Keep producer credits when they only appear in (Prod. …) / [Prod. …]
    s = _RE_PROD_PAREN.sub(r" prod \1 ", s)
    s = _RE_PROD.sub(" prod ", s)
    s = re.sub(r"\s*&\s*", " ", s)
    s = re.sub(r"\s*\+\s*", " ", s)
    s = re.sub(r",\s*", " ", s)
    # Parenthetical / bracket metadata: (Official MV), [HD], etc.
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = _RE_JUNK_PHRASES.sub(" ", s)
    # Remaining punctuation → spaces (keep letters/numbers across scripts)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def iter_files(root: Path, recursive: bool):
    """Walk files using OS Unicode APIs (reliable for Arabic folder/file names)."""
    root = root.resolve()

    def yield_file(entry_path: str) -> FileEntry | None:
        p = Path(entry_path)
        try:
            if not p.is_file():
                return None
            st = p.stat()
        except OSError:
            return None
        return FileEntry(path=p, name=p.name, size=st.st_size)

    if not recursive:
        try:
            with os.scandir(root) as scan:
                for entry in scan:
                    if entry.is_file(follow_symlinks=False):
                        fe = yield_file(entry.path)
                        if fe:
                            yield fe
        except OSError:
            return
        return

    try:
        for dirpath, _dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            for name in filenames:
                fe = yield_file(os.path.join(dirpath, name))
                if fe:
                    yield fe
    except OSError:
        return


@dataclass
class FileEntry:
    path: Path
    name: str
    size: int
    hash_hex: str | None = None


@dataclass
class DuplicateGroup:
    key: str
    mode: str
    files: list[FileEntry] = field(default_factory=list)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def reveal_path_in_file_manager(path: Path) -> str | None:
    """Reveal a file in the system file manager. None = success, else error text."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if not resolved.is_file():
        return f"File not found:\n{resolved}"

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["explorer", "/select,", os.fsdecode(resolved)],
                check=False,
            )
            return None
        except OSError as exc:
            return str(exc)

    if sys.platform == "darwin":
        try:
            subprocess.run(["open", "-R", resolved], check=False)
            return None
        except OSError as exc:
            return str(exc)

    uri = resolved.as_uri()
    if shutil.which("dbus-send"):
        try:
            result = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--print-reply",
                    "--dest=org.freedesktop.FileManager1",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{uri}",
                    "string:",
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return None
        except (OSError, subprocess.TimeoutExpired):
            pass

    path_str = os.fsdecode(resolved)
    for argv in (
        ["nautilus", "--select", path_str],
        ["nemo", "--select", path_str],
        ["dolphin", "--select", path_str],
    ):
        if shutil.which(argv[0]):
            try:
                subprocess.run(argv, check=False, timeout=10)
                return None
            except (OSError, subprocess.TimeoutExpired):
                continue

    parent = os.fsdecode(resolved.parent)
    if shutil.which("xdg-open"):
        try:
            subprocess.run(["xdg-open", parent], check=False, timeout=10)
            return None
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)
    return "No file manager found (install xdg-utils or a desktop file manager)."


def open_path_with_default_app(path: Path) -> str | None:
    """Open a file with the OS default application. None = success, else error text."""
    if not path.is_file():
        return f"File not found:\n{path}"
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            if not shutil.which("xdg-open"):
                return "xdg-open not found (install xdg-utils)."
            subprocess.run(["xdg-open", path], check=False)
        return None
    except OSError as exc:
        return str(exc)


def load_app_settings() -> dict:
    if not _SETTINGS_FILE.is_file():
        return {}
    try:
        with _SETTINGS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_app_settings(data: dict) -> None:
    try:
        with _SETTINGS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def file_hash(path: Path, cancel: threading.Event) -> str | None:
    h = hashlib.md5()
    try:
        with path.open("rb") as f:
            while True:
                if cancel.is_set():
                    return None
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


class DuplicateFinderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Duplicate File Finder")
        self.geometry("1100x700")
        self.minsize(900, 550)

        self._groups: list[DuplicateGroup] = []
        self._filtered_groups: list[DuplicateGroup] = []
        self._cancel = threading.Event()
        self._scan_thread: threading.Thread | None = None
        self._path_var = tk.StringVar()
        self._status_var = tk.StringVar(value="Enter a folder path and click Scan.")
        self._recursive_var = tk.BooleanVar(value=True)
        self._mode_var = tk.StringVar(value="name_similar")
        self._filter_ext_var = tk.StringVar()
        self._filter_name_var = tk.StringVar()
        self._filter_min_mb_var = tk.StringVar()
        self._check_dir_var = tk.StringVar()
        self._check_dir_subfolders_var = tk.BooleanVar(value=True)
        self._checked: set[str] = set()
        self._group_trees: list[ttk.Treeview] = []
        self._context_menu_iids: list[str] = []
        self._save_after_id: str | None = None

        self._ui_font = pick_unicode_font(self, 10)
        self._heading_font = pick_unicode_font(self, 10)
        self._heading_font.configure(weight="bold")

        self._build_ui()
        self._restore_settings()
        self._wire_autosave()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._maximize_window()

    def _maximize_window(self) -> None:
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def _apply_unicode_fonts(self, style: ttk.Style) -> None:
        family = self._ui_font.cget("family")
        size = self._ui_font.cget("size")
        style.configure(".", font=(family, size))
        style.configure("Treeview", font=(family, size), rowheight=max(22, size + 12))
        style.configure("Treeview.Heading", font=(family, size, "bold"))
        self.option_add("*Font", self._ui_font)
        self.option_add("*Message.Font", self._ui_font)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self._apply_unicode_fonts(style)

        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Folder path:").grid(row=0, column=0, sticky=tk.W)
        path_entry = ttk.Entry(top, textvariable=self._path_var, width=70)
        path_entry.grid(row=0, column=1, padx=6, sticky=tk.EW)
        ttk.Button(top, text="Browse…", command=self._browse).grid(row=0, column=2)
        self._scan_btn = ttk.Button(top, text="Scan", command=self._start_scan)
        self._scan_btn.grid(row=0, column=3, padx=(6, 0))
        self._stop_btn = ttk.Button(top, text="Stop", command=self._stop_scan)
        self._stop_btn.grid(row=0, column=4, padx=4)
        top.columnconfigure(1, weight=1)
        self._set_scan_buttons(scanning=False)

        opts = ttk.LabelFrame(self, text="Scan options", padding=8)
        opts.pack(fill=tk.X, padx=10, pady=(0, 6))

        ttk.Checkbutton(opts, text="Include subfolders", variable=self._recursive_var).pack(
            side=tk.LEFT, padx=(0, 16)
        )
        ttk.Label(opts, text="Find duplicates by:").pack(side=tk.LEFT)
        ttk.Radiobutton(opts, text="Content (hash)", value="content", variable=self._mode_var).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Radiobutton(opts, text="Exact file name", value="name", variable=self._mode_var).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Radiobutton(opts, text="Name + size", value="name_size", variable=self._mode_var).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Radiobutton(
            opts, text="Similar name", value="name_similar", variable=self._mode_var
        ).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(opts, text="Size", value="size", variable=self._mode_var).pack(
            side=tk.LEFT, padx=4
        )

        filt = ttk.LabelFrame(self, text="Filter results", padding=8)
        filt.pack(fill=tk.X, padx=10, pady=(0, 6))

        ttk.Label(filt, text="Extension:").pack(side=tk.LEFT)
        ttk.Entry(filt, textvariable=self._filter_ext_var, width=10).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(filt, text="Name contains:").pack(side=tk.LEFT)
        ttk.Entry(filt, textvariable=self._filter_name_var, width=28).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(filt, text="Min size (MB):").pack(side=tk.LEFT)
        ttk.Entry(filt, textvariable=self._filter_min_mb_var, width=8).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Button(filt, text="Apply filter", command=self._apply_filter).pack(side=tk.LEFT, padx=4)
        ttk.Button(filt, text="Clear filter", command=self._clear_filter).pack(side=tk.LEFT)

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill=tk.BOTH, expand=True)
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        self._groups_canvas = tk.Canvas(mid, highlightthickness=0)
        groups_vsb = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self._groups_canvas.yview)
        self._groups_canvas.configure(yscrollcommand=groups_vsb.set)
        self._groups_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        groups_vsb.grid(row=0, column=1, sticky=tk.NS)

        self._groups_inner = ttk.Frame(self._groups_canvas)
        self._groups_window = self._groups_canvas.create_window(
            (0, 0), window=self._groups_inner, anchor=tk.NW
        )

        def _on_inner_configure(_event: tk.Event) -> None:
            self._groups_canvas.configure(scrollregion=self._groups_canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            self._groups_canvas.itemconfigure(self._groups_window, width=event.width)

        self._groups_inner.bind("<Configure>", _on_inner_configure)
        self._groups_canvas.bind("<Configure>", _on_canvas_configure)

        for widget in (self._groups_canvas, self._groups_inner):
            self._bind_canvas_wheel(widget)

        sel = ttk.LabelFrame(self, text="Select files (checkbox)", padding=8)
        sel.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Button(sel, text="Check all", command=self._check_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sel, text="Uncheck all", command=self._uncheck_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(sel, text="Invert", command=self._invert_checks).pack(side=tk.LEFT, padx=4)
        ttk.Separator(sel, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(
            sel,
            text="Check duplicates (all but first in group)",
            command=self._check_duplicates_except_first,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            sel,
            text="Check all but newest in group",
            command=self._check_all_but_newest,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(sel, text="Check highlighted rows", command=self._check_highlighted).pack(
            side=tk.LEFT, padx=4
        )
        sel_row2 = ttk.Frame(sel)
        sel_row2.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(
            sel_row2, text="Check biggest in each group", command=self._check_biggest_in_group
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            sel_row2, text="Check smallest in each group", command=self._check_smallest_in_group
        ).pack(side=tk.LEFT, padx=4)
        sel_row3 = ttk.Frame(sel)
        sel_row3.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(sel_row3, text="Folder:").pack(side=tk.LEFT)
        ttk.Entry(sel_row3, textvariable=self._check_dir_var, width=44).pack(
            side=tk.LEFT, padx=(4, 4)
        )
        ttk.Button(sel_row3, text="Browse…", command=self._browse_check_dir).pack(side=tk.LEFT)
        ttk.Button(
            sel_row3, text="Use scan folder", command=self._use_scan_folder_for_check
        ).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(
            sel_row3,
            text="Include subfolders",
            variable=self._check_dir_subfolders_var,
        ).pack(side=tk.LEFT, padx=8)
        sel_row4 = ttk.Frame(sel)
        sel_row4.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(
            sel_row4,
            text="Check duplicates in folder",
            command=self._check_duplicates_in_folder,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            sel_row4,
            text="Check same-folder copies (keep 1 per folder)",
            command=self._check_same_folder_duplicates,
        ).pack(side=tk.LEFT, padx=4)

        actions = ttk.Frame(self, padding=10)
        actions.pack(fill=tk.X)

        ttk.Button(actions, text="Delete checked", command=self._delete_selected).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="Keep newest in each group — delete others",
            command=self._delete_keep_newest,
        ).pack(side=tk.LEFT, padx=8)
        ttk.Button(
            actions, text="Keep first in each group — delete others",
            command=self._delete_keep_first,
        ).pack(side=tk.LEFT)

        status = ttk.Frame(self, padding=(10, 6))
        status.pack(fill=tk.X)
        ttk.Label(status, textvariable=self._status_var).pack(anchor=tk.W)
        self._progress = ttk.Progressbar(status, mode="indeterminate")
        self._progress.pack(fill=tk.X, pady=(4, 0))

    def _restore_settings(self) -> None:
        data = load_app_settings()
        if folder := data.get("folder_path", ""):
            self._path_var.set(folder)
        self._recursive_var.set(bool(data.get("recursive", True)))
        mode = data.get("scan_mode", "name_similar")
        if mode in _VALID_SCAN_MODES:
            self._mode_var.set(mode)
        self._filter_ext_var.set(data.get("filter_ext", ""))
        self._filter_name_var.set(data.get("filter_name", ""))
        self._filter_min_mb_var.set(data.get("filter_min_mb", ""))
        if check_dir := data.get("check_dir", ""):
            self._check_dir_var.set(check_dir)
        elif folder:
            self._check_dir_var.set(folder)
        self._check_dir_subfolders_var.set(bool(data.get("check_dir_subfolders", True)))
        geom = data.get("geometry")
        if isinstance(geom, str) and geom:
            try:
                self.geometry(geom)
            except tk.TclError:
                pass
        if folder:
            self._status_var.set("Restored last folder path — click Scan.")

    def _collect_settings(self) -> dict:
        return {
            "folder_path": self._path_var.get().strip(),
            "recursive": self._recursive_var.get(),
            "scan_mode": self._mode_var.get(),
            "filter_ext": self._filter_ext_var.get().strip(),
            "filter_name": self._filter_name_var.get().strip(),
            "filter_min_mb": self._filter_min_mb_var.get().strip(),
            "check_dir": self._check_dir_var.get().strip(),
            "check_dir_subfolders": self._check_dir_subfolders_var.get(),
            "geometry": self.geometry(),
        }

    def _persist_settings(self) -> None:
        self._save_after_id = None
        save_app_settings(self._collect_settings())

    def _schedule_save_settings(self, *_args: object) -> None:
        if self._save_after_id:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(800, self._persist_settings)

    def _wire_autosave(self) -> None:
        for var in (
            self._path_var,
            self._mode_var,
            self._filter_ext_var,
            self._filter_name_var,
            self._filter_min_mb_var,
            self._check_dir_var,
        ):
            var.trace_add("write", self._schedule_save_settings)
        self._recursive_var.trace_add("write", self._schedule_save_settings)
        self._check_dir_subfolders_var.trace_add("write", self._schedule_save_settings)

    def _on_close(self) -> None:
        if self._save_after_id:
            self.after_cancel(self._save_after_id)
        self._persist_settings()
        self.destroy()

    def _browse(self) -> None:
        folder = filedialog.askdirectory(title="Select folder to scan")
        if folder:
            self._path_var.set(folder)

    def _browse_check_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select folder for duplicate selection")
        if folder:
            self._check_dir_var.set(folder)

    def _set_scan_buttons(self, *, scanning: bool) -> None:
        if scanning:
            self._scan_btn.configure(state="disabled")
            self._stop_btn.configure(state="normal")
        else:
            self._scan_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")

    def _use_scan_folder_for_check(self) -> None:
        raw = self._path_var.get().strip().strip('"')
        if not raw:
            messagebox.showwarning("Folder", "Enter a scan folder path first.")
            return
        self._check_dir_var.set(raw)

    def _stop_scan(self) -> None:
        self._cancel.set()
        self._status_var.set("Stopping scan…")

    def _start_scan(self) -> None:
        raw = self._path_var.get().strip().strip('"')
        if not raw:
            messagebox.showwarning("Path required", "Please enter or browse to a folder path.")
            return
        root = Path(raw)
        if not root.is_dir():
            messagebox.showerror("Invalid path", f"Not a folder:\n{root}")
            return

        if self._scan_thread and self._scan_thread.is_alive():
            messagebox.showinfo("Scan in progress", "A scan is already running. Click Stop first.")
            return

        self._cancel.clear()
        self._groups.clear()
        self._filtered_groups.clear()
        self._checked.clear()
        self._refresh_tree()
        self._progress.start(8)
        self._status_var.set("Scanning…")
        self._set_scan_buttons(scanning=True)

        self._scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(root, self._recursive_var.get(), self._mode_var.get()),
            daemon=True,
        )
        self._scan_thread.start()

    def _scan_worker(self, root: Path, recursive: bool, mode: str) -> None:
        try:
            files = self._collect_files(root, recursive)
            if self._cancel.is_set():
                self.after(0, lambda: self._scan_done(cancelled=True))
                return

            groups = self._find_duplicates(files, mode)
            self._groups = groups
            self._filtered_groups = list(groups)
            self.after(0, lambda: self._scan_done(cancelled=False))
        except Exception as exc:
            self.after(0, lambda: self._scan_error(str(exc)))

    def _collect_files(self, root: Path, recursive: bool) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for fe in iter_files(root, recursive):
            if self._cancel.is_set():
                break
            entries.append(fe)
        return entries

    def _find_duplicates(self, files: list[FileEntry], mode: str) -> list[DuplicateGroup]:
        buckets: dict[str, list[FileEntry]] = defaultdict(list)

        if mode == "content":
            size_map: dict[int, list[FileEntry]] = defaultdict(list)
            for fe in files:
                size_map[fe.size].append(fe)

            candidates = [g for g in size_map.values() if len(g) > 1]
            total = sum(len(g) for g in candidates)
            done = 0

            for group in candidates:
                hash_map: dict[str, list[FileEntry]] = defaultdict(list)
                for fe in group:
                    if self._cancel.is_set():
                        return []
                    done += 1
                    if done % 5 == 0:
                        pct = int(100 * done / max(total, 1))
                        self.after(
                            0,
                            lambda p=pct: self._status_var.set(f"Hashing files… {p}%"),
                        )
                    h = file_hash(fe.path, self._cancel)
                    if h is None:
                        continue
                    fe.hash_hex = h
                    hash_map[h].append(fe)

                for h, items in hash_map.items():
                    if len(items) > 1:
                        buckets[h].extend(items)
        elif mode == "name":
            for fe in files:
                buckets[norm_text(fe.name)].append(fe)
        elif mode == "name_similar":
            for fe in files:
                key = normalize_media_filename(fe.name)
                if key:
                    buckets[key].append(fe)
        elif mode == "name_size":
            for fe in files:
                key = f"{norm_text(fe.name)}|{fe.size}"
                buckets[key].append(fe)
        elif mode == "size":
            for fe in files:
                buckets[str(fe.size)].append(fe)

        groups: list[DuplicateGroup] = []
        idx = 1
        for key, items in buckets.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda x: norm_text(str(x.path)))
            groups.append(DuplicateGroup(key=key, mode=mode, files=items))
            idx += 1

        if mode == "size":
            groups.sort(key=lambda g: (-g.files[0].size, -len(g.files), norm_text(g.files[0].name)))
        else:
            groups.sort(key=lambda g: (-len(g.files), norm_text(g.files[0].name)))
        for i, g in enumerate(groups, start=1):
            g.key = str(i)
        return groups

    def _scan_done(self, cancelled: bool) -> None:
        self._progress.stop()
        self._set_scan_buttons(scanning=False)
        if cancelled:
            self._status_var.set("Scan cancelled.")
            return
        self._apply_filter()
        dup_files = sum(len(g.files) for g in self._filtered_groups)
        wasted = sum(
            (len(g.files) - 1) * g.files[0].size for g in self._filtered_groups if g.files
        )
        self._status_var.set(
            f"Found {len(self._filtered_groups)} duplicate group(s), "
            f"{dup_files} file(s). Recoverable space: ~{human_size(wasted)}."
        )

    def _scan_error(self, msg: str) -> None:
        self._progress.stop()
        self._set_scan_buttons(scanning=False)
        self._status_var.set("Scan failed.")
        messagebox.showerror("Scan error", msg)

    def _passes_filter(self, fe: FileEntry) -> bool:
        ext = norm_text(self._filter_ext_var.get().strip().lstrip("."))
        if ext and not norm_text(fe.name).endswith(f".{ext}"):
            return False
        needle = norm_text(self._filter_name_var.get().strip())
        if needle and needle not in norm_text(fe.name):
            return False
        min_mb = self._filter_min_mb_var.get().strip()
        if min_mb:
            try:
                if fe.size < float(min_mb) * 1024 * 1024:
                    return False
            except ValueError:
                pass
        return True

    def _apply_filter(self) -> None:
        self._filtered_groups = []
        for g in self._groups:
            filtered = [f for f in g.files if self._passes_filter(f)]
            if len(filtered) >= 2:
                ng = DuplicateGroup(key=g.key, mode=g.mode, files=filtered)
                self._filtered_groups.append(ng)
        self._refresh_tree()

    def _clear_filter(self) -> None:
        self._filter_ext_var.set("")
        self._filter_name_var.set("")
        self._filter_min_mb_var.set("")
        self._filtered_groups = list(self._groups)
        self._refresh_tree()
        self._status_var.set(
            f"Showing all {len(self._filtered_groups)} group(s) (filter cleared)."
        )

    def _wheel_delta(self, event: tk.Event) -> int:
        return int(-1 * (event.delta / 120))

    def _scroll_canvas_wheel(self, event: tk.Event) -> str:
        self._groups_canvas.yview_scroll(self._wheel_delta(event), "units")
        return "break"

    def _bind_canvas_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._scroll_canvas_wheel)

    def _on_tree_mousewheel(self, event: tk.Event, tree: ttk.Treeview) -> str:
        delta = self._wheel_delta(event)
        first, last = tree.yview()
        if delta > 0 and first > 0.0:
            tree.yview_scroll(delta, "units")
        elif delta < 0 and last < 1.0:
            tree.yview_scroll(delta, "units")
        else:
            self._groups_canvas.yview_scroll(delta, "units")
        return "break"

    def _bind_tree_wheel(self, tree: ttk.Treeview, *extra: tk.Misc) -> None:
        handler = lambda e, t=tree: self._on_tree_mousewheel(e, t)
        tree.bind("<MouseWheel>", handler)
        for widget in extra:
            widget.bind("<MouseWheel>", handler)

    def _group_panel_title(self, g: DuplicateGroup) -> str:
        n = len(g.files)
        size = human_size(g.files[0].size) if g.files else "?"
        sample = g.files[0].name if g.files else ""
        if len(sample) > 55:
            sample = sample[:52] + "…"
        return f"Group {g.key} — {n} files — {size} — {sample}"

    def _create_group_tree(self, parent: ttk.LabelFrame, g: DuplicateGroup) -> ttk.Treeview:
        cols = ("checked", "name", "size", "dirname", "path")
        height = min(max(len(g.files), 2), 12)
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.X, expand=True)
        tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            selectmode="extended",
            height=height,
        )
        tree.heading("checked", text="")
        tree.heading("name", text="File name")
        tree.heading("size", text="Size")
        tree.heading("dirname", text="Folder")
        tree.heading("path", text="Full path")
        tree.column("checked", width=32, anchor=tk.CENTER, stretch=False)
        tree.column("name", width=220)
        tree.column("size", width=80, anchor=tk.E)
        tree.column("dirname", width=240)
        tree.column("path", width=320)
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)
        tree_frame.columnconfigure(0, weight=1)
        tree.bind("<Button-1>", self._on_tree_click, add=True)
        tree.bind("<Button-3>", self._on_tree_right_click)
        tree.bind("<space>", self._on_tree_space)
        self._bind_tree_wheel(tree, tree_frame)
        tree.tag_configure("dup", foreground="#b00020")
        return tree

    def _tree_for_iid(self, iid: str) -> ttk.Treeview | None:
        for tree in self._group_trees:
            if tree.exists(iid):
                return tree
        return None

    def _iid_exists(self, iid: str) -> bool:
        return self._tree_for_iid(iid) is not None

    def _checkbox_display(self, iid: str) -> str:
        return CHK_ON if iid in self._checked else CHK_OFF

    def _set_checked(self, iid: str, checked: bool) -> None:
        if checked:
            self._checked.add(iid)
        else:
            self._checked.discard(iid)
        tree = self._tree_for_iid(iid)
        if tree is not None:
            vals = list(tree.item(iid, "values"))
            vals[0] = self._checkbox_display(iid)
            tree.item(iid, values=vals)

    def _toggle_checked(self, iid: str) -> None:
        self._set_checked(iid, iid not in self._checked)

    def _on_tree_click(self, event: tk.Event) -> str | None:
        tree = event.widget
        if not isinstance(tree, ttk.Treeview):
            return None
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        if tree.identify_column(event.x) != "#1":
            return None
        row = tree.identify_row(event.y)
        if not row:
            return None
        self._toggle_checked(row)
        return "break"

    def _on_tree_space(self, event: tk.Event) -> None:
        tree = event.widget
        if not isinstance(tree, ttk.Treeview):
            return
        for iid in tree.selection():
            self._toggle_checked(iid)

    def _on_tree_right_click(self, event: tk.Event) -> str | None:
        tree = event.widget
        if not isinstance(tree, ttk.Treeview):
            return None
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        row = tree.identify_row(event.y)
        if not row:
            return None
        if row not in tree.selection():
            tree.selection_set(row)
        self._show_file_context_menu(tree, event)
        return "break"

    def _context_target_iids(self, tree: ttk.Treeview) -> list[str]:
        sel = list(tree.selection())
        return sel if sel else list(self._context_menu_iids)

    def _show_file_context_menu(self, tree: ttk.Treeview, event: tk.Event) -> None:
        iids = self._context_target_iids(tree)
        if not iids:
            return
        self._context_menu_iids = iids
        paths = [Path(os.fsdecode(iid)) for iid in iids if self._iid_exists(iid)]
        if not paths:
            return

        all_checked = all(iid in self._checked for iid in iids)
        all_unchecked = all(iid not in self._checked for iid in iids)
        if all_checked:
            check_label = "Uncheck"
        elif all_unchecked:
            check_label = "Check"
        else:
            check_label = "Check/Uncheck"

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label=check_label,
            command=lambda: self._context_toggle_check(iids),
        )
        menu.add_separator()
        menu.add_command(
            label="Reveal in file browser",
            command=lambda: self._reveal_in_file_browser(paths[0]),
        )
        menu.add_command(label="Open", command=lambda: self._open_paths(paths))
        menu.add_separator()
        menu.add_command(
            label="Remove from this list",
            command=lambda: self._remove_paths_from_list(paths),
        )
        menu.add_command(
            label="Delete",
            command=lambda: self._delete_paths(paths),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _context_toggle_check(self, iids: list[str]) -> None:
        if all(iid in self._checked for iid in iids):
            for iid in iids:
                self._set_checked(iid, False)
        else:
            for iid in iids:
                self._set_checked(iid, True)
        self._update_check_status()

    def _reveal_in_file_browser(self, path: Path) -> None:
        err = reveal_path_in_file_manager(path)
        if err:
            messagebox.showerror("Reveal", err)

    def _open_paths(self, paths: list[Path]) -> None:
        errors: list[str] = []
        for path in paths:
            err = open_path_with_default_app(path)
            if err:
                errors.append(err)
        if errors:
            messagebox.showerror(
                "Open",
                "Could not open:\n" + "\n".join(errors[:6]),
            )

    def _remove_paths_from_list(self, paths: list[Path]) -> None:
        if not paths:
            return
        for p in paths:
            self._checked.discard(os.fsdecode(p))
        self._remove_deleted_from_groups(paths)
        self._refresh_tree()
        n = len(paths)
        self._status_var.set(
            f"Removed {n} file(s) from the list (not deleted from disk). "
            f"Groups remaining: {len(self._filtered_groups)}."
        )

    def _tree_iids(self) -> list[str]:
        iids: list[str] = []
        for tree in self._group_trees:
            iids.extend(tree.get_children())
        return iids

    def _check_all(self) -> None:
        for iid in self._tree_iids():
            self._set_checked(iid, True)
        self._update_check_status()

    def _uncheck_all(self) -> None:
        for iid in self._tree_iids():
            self._set_checked(iid, False)
        self._update_check_status()

    def _invert_checks(self) -> None:
        for iid in self._tree_iids():
            self._toggle_checked(iid)
        self._update_check_status()

    def _check_iids(self, iids: list[str]) -> None:
        for iid in iids:
            self._set_checked(iid, True)
        self._update_check_status()

    def _check_duplicates_except_first(self) -> None:
        iids: list[str] = []
        for g in self._filtered_groups:
            for fe in g.files[1:]:
                iids.append(os.fsdecode(fe.path))
        self._uncheck_all()
        self._check_iids(iids)

    def _check_all_but_newest(self) -> None:
        iids: list[str] = []
        for g in self._filtered_groups:
            if len(g.files) < 2:
                continue
            sorted_files = sorted(g.files, key=lambda f: f.path.stat().st_mtime, reverse=True)
            iids.extend(os.fsdecode(f.path) for f in sorted_files[1:])
        self._uncheck_all()
        self._check_iids(iids)

    def _check_by_size_in_groups(self, pick: str) -> None:
        iids: list[str] = []
        for g in self._filtered_groups:
            if not g.files:
                continue
            sizes = [fe.size for fe in g.files]
            target = max(sizes) if pick == "max" else min(sizes)
            iids.extend(os.fsdecode(fe.path) for fe in g.files if fe.size == target)
        self._uncheck_all()
        self._check_iids(iids)

    def _check_biggest_in_group(self) -> None:
        self._check_by_size_in_groups("max")

    def _check_smallest_in_group(self) -> None:
        self._check_by_size_in_groups("min")

    def _resolve_check_dir(self) -> Path | None:
        raw = self._check_dir_var.get().strip().strip('"')
        if not raw:
            raw = self._path_var.get().strip().strip('"')
        if not raw:
            messagebox.showwarning(
                "Folder",
                "Enter a folder path above, or use “Use scan folder”.",
            )
            return None
        path = Path(raw)
        if not path.is_dir():
            messagebox.showerror("Folder", f"Not a folder:\n{path}")
            return None
        return path.resolve()

    def _file_in_dir(self, fe: FileEntry, target: Path, include_sub: bool) -> bool:
        try:
            parent = fe.path.parent.resolve()
            target = target.resolve()
        except OSError:
            return False
        if parent == target:
            return True
        if not include_sub:
            return False
        try:
            parent.relative_to(target)
            return True
        except ValueError:
            return False

    def _check_duplicates_in_folder(self) -> None:
        target = self._resolve_check_dir()
        if target is None:
            return
        include_sub = self._check_dir_subfolders_var.get()
        iids: list[str] = []
        for g in self._filtered_groups:
            for fe in g.files:
                if self._file_in_dir(fe, target, include_sub):
                    iids.append(os.fsdecode(fe.path))
        if not iids:
            messagebox.showinfo(
                "No matches",
                f"No duplicate files in the current results are under:\n{target}",
            )
            return
        self._uncheck_all()
        self._check_iids(iids)

    def _check_same_folder_duplicates(self) -> None:
        """Within each group, check extra copies that sit in the same folder."""
        iids: list[str] = []
        for g in self._filtered_groups:
            by_dir: dict[str, list[FileEntry]] = defaultdict(list)
            for fe in g.files:
                try:
                    dir_key = os.fsdecode(fe.path.parent.resolve())
                except OSError:
                    dir_key = os.fsdecode(fe.path.parent)
                by_dir[dir_key].append(fe)
            for entries in by_dir.values():
                if len(entries) < 2:
                    continue
                entries.sort(key=lambda f: norm_text(str(f.path)))
                for fe in entries[1:]:
                    iids.append(os.fsdecode(fe.path))
        if not iids:
            messagebox.showinfo(
                "No matches",
                "No duplicate files share the same folder within a group.",
            )
            return
        self._uncheck_all()
        self._check_iids(iids)

    def _check_highlighted(self) -> None:
        sel: list[str] = []
        for tree in self._group_trees:
            sel.extend(tree.selection())
        if not sel:
            messagebox.showinfo("Select files", "Highlight one or more rows in the list first.")
            return
        self._check_iids(sel)

    def _update_check_status(self) -> None:
        n = len(self._checked)
        if n:
            self._status_var.set(f"{n} file(s) checked — click Delete checked to remove them.")

    def _refresh_tree(self) -> None:
        kept_checked = set(self._checked)
        for child in self._groups_inner.winfo_children():
            child.destroy()
        self._group_trees.clear()
        self._checked.clear()

        if not self._filtered_groups:
            ttk.Label(
                self._groups_inner,
                text="No duplicate groups — scan a folder or adjust filters.",
            ).pack(pady=24)
            return

        for g in self._filtered_groups:
            panel = ttk.LabelFrame(
                self._groups_inner,
                text=self._group_panel_title(g),
                padding=6,
            )
            panel.pack(fill=tk.X, padx=4, pady=6)
            self._bind_canvas_wheel(panel)
            tree = self._create_group_tree(panel, g)
            self._group_trees.append(tree)

            for fe in g.files:
                display_path = os.fsdecode(fe.path)
                display_dir = os.fsdecode(fe.path.parent)
                if display_path in kept_checked:
                    self._checked.add(display_path)
                tree.insert(
                    "",
                    tk.END,
                    iid=display_path,
                    values=(
                        self._checkbox_display(display_path),
                        fe.name,
                        human_size(fe.size),
                        display_dir,
                        display_path,
                    ),
                    tags=("dup",),
                )

        self._groups_canvas.yview_moveto(0)

    def _checked_paths(self) -> list[Path]:
        return [Path(os.fsdecode(iid)) for iid in self._checked if self._iid_exists(iid)]

    def _selected_paths(self) -> list[Path]:
        return self._checked_paths()

    def _delete_paths(self, paths: list[Path]) -> None:
        if not paths:
            messagebox.showinfo(
                "Nothing checked",
                "Check one or more files (click ☐ in the list or use Select buttons), then delete.",
            )
            return
        preview = "\n".join(str(p) for p in paths[:12])
        if len(paths) > 12:
            preview += f"\n… and {len(paths) - 12} more"
        if not messagebox.askyesno(
            "Confirm delete",
            f"Permanently delete {len(paths)} file(s)?\n\n{preview}",
            icon="warning",
        ):
            return

        errors: list[str] = []
        deleted = 0
        for p in paths:
            try:
                p.unlink()
                deleted += 1
                self._checked.discard(os.fsdecode(p))
            except OSError as exc:
                errors.append(f"{p}\n  {exc}")

        self._remove_deleted_from_groups(paths)
        self._refresh_tree()
        if errors:
            messagebox.showwarning(
                "Partial delete",
                f"Deleted {deleted} file(s).\n\nFailed:\n" + "\n".join(errors[:8]),
            )
        else:
            messagebox.showinfo("Done", f"Deleted {deleted} file(s).")
        self._status_var.set(f"Deleted {deleted} file(s). Groups remaining: {len(self._filtered_groups)}.")

    def _remove_deleted_from_groups(self, paths: list[Path]) -> None:
        deleted_set = {os.fsdecode(p) for p in paths}
        for coll in (self._groups, self._filtered_groups):
            new_groups: list[DuplicateGroup] = []
            for g in coll:
                remaining = [f for f in g.files if os.fsdecode(f.path) not in deleted_set]
                if len(remaining) >= 2:
                    g.files = remaining
                    new_groups.append(g)
            coll[:] = new_groups

    def _delete_selected(self) -> None:
        self._delete_paths(self._selected_paths())

    def _delete_keep_newest(self) -> None:
        to_delete: list[Path] = []
        for g in self._filtered_groups:
            if len(g.files) < 2:
                continue
            sorted_files = sorted(g.files, key=lambda f: f.path.stat().st_mtime, reverse=True)
            to_delete.extend(f.path for f in sorted_files[1:])
        self._delete_paths(to_delete)

    def _delete_keep_first(self) -> None:
        to_delete: list[Path] = []
        for g in self._filtered_groups:
            if len(g.files) < 2:
                continue
            to_delete.extend(f.path for f in g.files[1:])
        self._delete_paths(to_delete)


def main() -> None:
    app = DuplicateFinderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
