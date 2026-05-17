Duplicate File Finder
=====================

Quick start
-----------
1. Double-click run.bat  (or: python duplicate_finder.py)
   Or run the built app: dist\Duplicate File Finder.exe
2. Enter a folder path or click Browse
3. Click Scan

Build .exe (Windows)
--------------------
1. Double-click build.bat  (needs Python on PATH)
2. Output: dist\Duplicate File Finder.exe
3. You can copy the .exe anywhere; settings save as duplicate_finder_settings.json
   in the same folder as the .exe

Scan modes
----------
- Content (hash)  — same file bytes (true duplicates), even if names differ
- Exact file name — same filename in different folders
- Name + size     — same name and same size
- Similar name    — same song/video after normalizing messy titles (quotes,
                    feat./&, 720p, "Official Music Video", parentheses, etc.)
- Size            — same file size (may be different names; review before delete)

Each duplicate group appears in its own labeled list (scroll down to see all groups).
Each list shows checkboxes, file name, size, folder, and full path.

Filters
-------
- Extension     e.g. mp4
- Name contains e.g. Awich
- Min size (MB) e.g. 100

Checkboxes and selection
------------------------
- Click ☐ in the grid to check/uncheck a file (or press Space on highlighted rows).
- Check all / Uncheck all / Invert
- Check duplicates — all but the first file in each group
- Check all but newest — older copies in each group (by modified time)
- Check biggest / smallest in each group — the largest or smallest file per group
  (if several tie on size, all tied files are checked)
- Check duplicates in folder — checks copies under the Folder path (scan folder
  is used if Folder is empty); optional “Include subfolders”
- Check same-folder copies — within each group, when several duplicates sit in
  one folder, checks all but one per folder
- Check highlighted rows — checks the rows you clicked in the list

Delete options
--------------
- Delete checked — removes checked files only
- Keep newest in each group — deletes older copies (no checkboxes needed)
- Keep first in each group — keeps the first listed file per group

Example path (your music folder):
  d:\Music

Arabic file names
-----------------
- UI uses Segoe UI / Tahoma so Arabic shows correctly in the list.
- Folders and files with Arabic names are read via Windows Unicode APIs.
- Filter "Name contains" accepts Arabic text (e.g. part of a song title).

Saved settings
--------------
- Folder path, scan mode, subfolders option, filters, and check-folder path are
  saved to duplicate_finder_settings.json next to the app.
- Settings reload automatically when you open the app again.

Notes
-----
- Deletion is permanent (not Recycle Bin). Confirm carefully.
- Large folders take time when using Content (hash) mode.
- Use Stop to cancel a long scan.

License
-------
This project is licensed under the [MIT License](LICENSE).
