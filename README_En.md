# VideoChecker — Video Integrity Checker

> English version of [README.md](README.md) (Chinese original).
>
> PS: This tool is mainly made for fellow digital hoarders 0.0

Checks whether video files on a disk/folder are intact (detects truncation and corruption caused by interrupted downloads or unfinished recordings),
lists the results for manual review and deletion (moved to the Recycle Bin, always recoverable).

## Usage

Double-click `VideoChecker.exe` — no installation required.

1. Choose a scan location (disk dropdown / pick a folder / type a path)
2. Check the video formats you want to scan
3. Click "Start Scan" and wait for it to finish
4. Filter and select rows in the results table, then click "Delete Selected (to Recycle Bin)"

Tips:
- First launch takes 3–5 seconds (single-file EXE self-extraction)
- Deletion only moves files to the Recycle Bin — nothing is destroyed directly
- Double-click a result row to open the file's location

## Detection Capabilities

Verdicts come in three levels:
- **✔ OK**: container structure is complete and well-formed
- **⚠ Structural issues (playable)**: tail truncation, missing index, trailing junk bytes, etc. — players such as PotPlayer/VLC can usually play these fine; deletion not recommended
- **✖ Broken (deletion recommended)**: missing moov/moof index, severely damaged structure, all-zero content — most likely unplayable

| Format | Detection depth |
|--------|-----------------|
| MP4 / MOV / M4V / 3GP | Deep: box structure traversal + moov/moof index validation |
| MKV / WebM | Deep: EBML structure traversal |
| AVI | Deep: RIFF structure traversal + idx1 index validation |
| FLV | Deep: tag chain traversal + PreviousTagSize validation |
| TS / MTS / M2TS | Packet alignment + sync bytes at head and tail |
| WMV / MPG / VOB / RM(RMVB) | Header-only validation (marked "structural issues"; deep validation not possible) |

Container detection relies on content sniffing (magic numbers take priority over file extensions), so files with mislabeled extensions are still inspected with the correct parser. A one-click "Fix mislabeled extensions" feature is included.

Full-disk scans automatically skip system directories such as Windows, Program Files, and $RECYCLE.BIN.

## Source Code

- `detector.py` — detection core: container structure validation for each format, drive enumeration, Recycle Bin deletion
- `video_checker_gui.py` — native desktop UI (Tkinter, dark theme)
- `test_detection.py` — unit tests for detection logic (synthetic positive/negative samples, 23 cases + 10 container-sniffing cases)
- `test_e2e.py` — end-to-end test of the legacy web architecture (reference only)
- `build.bat` — one-click build script

(The legacy web-architecture sources `server.py` + `index.html` are kept locally but excluded from this repository.)

## Requirements (for rebuilding)

- Windows 10/11, Python 3.10+ (standard library only, no third-party dependencies)
- Run `pip install pyinstaller`, then run `build.bat`

Verification: `python test_detection.py` should report 23/23 passed (plus 10/10 sniffing tests).
