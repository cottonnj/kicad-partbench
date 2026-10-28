# Changelog

Versions before 6.1.0 were numbered inside the script header; see `git log` for individual changes.

## 6.1.x (unreleased)

- Renamed to **KiCad PartBench**. Settings and log file names are unchanged.
- Runs on Python 3.10 and 3.11 again (a multi-line f-string needed 3.12).
- CSV BOM import no longer needs pandas. Empty cells stay empty, and numeric-looking MPNs such as `0603` stay text.
- Read-only dropdowns are readable in the dark theme.
- The About box shows the correct version.
- Added unit tests and CI.
- Added feature tests that drive the real app against a generated practice library (`tests/practice_lib.py`, `tests/gui_harness.py`). Confirmed bugs are tracked as expected failures and in issues #2–#5.
- The 3D Models, BOM Manager and Passives tabs are marked experimental (#1).
- **Multi-part CPL** tab (experimental): place one schematic symbol as several parts in the JLCPCB plugin's BOM/CPL, such as the two 100-pin connectors of a Raspberry Pi CM5. Pad-range or offset placement, a built-in CM5 preset and your own presets, a board preview, a CSV diff and `.bak` backups. See `docs/multipart-cpl-spec.md`.
- **Fix: imported 3D models didn't show in KiCad.** The model was copied into `<library>/3d/step`, but the footprint kept the vendor's own model path (`${KIPRJMOD}/STEP/…`, `C:\…\SamacSys_Parts.3dshapes\…`). Import now repoints that line at the copy and keeps the vendor's offset/scale/rotation. For parts imported before this fix, run **3D Models → Scan → Select All Suggested → Apply**.
- Relative model paths (`../3d/step/…`) are the default everywhere, including the Assign 3D Model dialog.

## 6.1.0 (2026-09-22)

- **Inbox** tab: Downloads and zips folders side by side. Move, return or recycle zips; **Import Zips** without leaving the tab; copy `Lib:Symbol` or footprint names.
- Detects the symbol-library nickname from `sym-lib-table`.
- The tab-change handler matches tabs by label instead of index.

## 6.0.0 (2026-06-08)

- **Passives** tab: scan a project's R/C/L, group them by value and package, and apply MPN, LCSC and spec fields to every ticked instance.
- Global passives library (`~/.kicad-library-tool/passives.json`) with a Library Manager, a unit normalizer, overwrite confirmation, **Save All Complete to Library**, status filters and package auto-complete.

## 5.6.0 (2025-11-08)

- **3D Models** tab: checks every footprint's model path, finds missing models (exact and fuzzy match), and previews fixes for corrupted paths.
- Fixed Windows backslashes left in imported model paths (KiCad read `\3` as an escape character).
- Import skip-list (`.kicad_personal_import_log.json`) so incremental imports only process new packages.
- Detailed import summary popup.

## 5.4 (2025-11-05)

- Assign 3D models by hand, delete parts, clean up duplicates, and validate files.

## 5.2 (2025-10-30)

- Scan footprints and update 3D models in bulk from a checklist.

## 5.1 (2025-10-29)

- **BOM Manager** tab: push Octopart BOM MPNs into schematics.

## 5.0 (2025-10-27)

- Rewrite with a tabbed dark-theme UI (Import / Library / Settings / Results).
- Library repair and restore; MPN field standardization.

## 4.x (2025-10-06)

- Pairs 3D models with footprints during import. Adds a 3D root folder, relative model paths and Delete Parts.
- Sets the KiCad path variable and rewrites `fp-lib-table`.
- Summary and Verbose log tabs.

## 3.x (2025-10-03)

- Grew from a command-line importer into a GUI with a strict symbol-library writer, content-hash deduplication, dry run, package pruning, a library parts viewer and footprint 3D audit.
