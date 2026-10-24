# Plan: Multi-part CPL tab

Implementation plan for [multipart-cpl-spec.md](multipart-cpl-spec.md). Written 2026-10-22.

## Changes from the spec

- **Presets.** A built-in CM5 / CM4 preset fills the sub-parts table in one click.
  Users can save their own presets.
- **Manual offset mode.** Each sub-part is placed either from a pad range (as in the
  spec) or from a typed X/Y/rotation offset relative to the parent footprint's origin.
- **Auto-split is dropped.** The preset covers the CM5, and other cases are rare
  enough that typing a pad range or offset is fine.

## Things the plan accounts for

1. **The existing paren matcher doesn't skip quoted strings.** `_find_matching_paren`
   counts parentheses inside quoted strings too. Footprint properties in a
   `.kicad_pcb` often contain them (descriptions, URLs). Add a new matcher that skips
   strings (`_sexp_block_end`) for this tab, and leave the old one alone so no other
   tab changes behaviour.
2. **Pad angles in the file are absolute.** In KiCad 6 and later, a pad's
   `(at x y angle)` already includes the footprint rotation, while its x/y stay
   local. A pad's bounding box is rotated by its own absolute angle.
3. **BOM rows can list several parts.** To patch safely, remove the sub-part and
   parent refs from every row they appear in, drop rows that end up empty, then add
   one fresh row per (comment, footprint, LCSC) group. Patching once or twice then
   gives the same file.
4. **There may not be a quantity column.** Only write quantity if the plugin's BOM
   header has one.

## Config (`<project>.multipart.json`)

Same as the spec, with these additions:

- `"mode": "pads" | "offset"` on each sub-part. The default is `"pads"`.
  - `pads`: the centre comes from the pad bounding boxes, and `xy_offset_mm` is a
    small nudge on top.
  - `offset`: `xy_offset_mm` and `rotation_offset` are measured from the parent
    footprint's origin and turn with it. `pads` is not needed.
- An optional top-level `"preset"` name on each parent, for reference.

It stays at `"version": 1`, because nothing has written the file yet.

## Presets

Built-in presets live in a `MULTIPART_PRESETS` table in the code. User presets are
saved in `~/.kicad-library-tool/multipart_presets.json`, next to the passives library.

```python
MULTIPART_PRESETS = {
    "Raspberry Pi CM5 / CM4 (2× 100-pin B2B)": {
        "subparts": [
            {"ref": "J6", "pads": "101-200", "expect_local_mm": [33.5, -21.5]},
            {"ref": "J7", "pads": "1-100",   "expect_local_mm": [-0.5, -21.5]},
        ],
        "comment": "", "footprint": "", "lcsc": "", "rotation_offset": 0,
    },
}
```

- **The position still comes from the pads.** `expect_local_mm` is only a
  cross-check. If the computed centre is more than 0.1 mm from it, Preview shows a
  warning. That catches a footprint drawn with a different origin, or J6 and J7
  swapped.
- **MPN/LCSC stay blank** until spec question 1 is answered. After that, the answer
  and the JLC rotation confirmed in JLC's placement preview go into the preset.
- **CM4:** check the pad numbering on a CM4 board before saying the preset covers it.

## Phase 0: Check the assumptions

- Read Bouni's `fabrication.py` (`get_position`, `generate_cpl`, `fix_rotation`,
  `reposition`) and record:
  - the exact CPL/BOM headers
  - how numbers are formatted
  - how the layer is spelled
  - the output file names and folder
- Fixtures from M-050 Rev-12:
  - a trimmed `.kicad_pcb` with only `setup` and `Module1`
  - the real `BOM-*.csv` and `CPL-*.csv`
  - if possible, a bottom-side multi-pad footprint, to check how pads on B.Cu are
    stored
- The spec's maths has been checked and is consistent. With θ=90, local
  (−0.5, −21.5) gives board (82.45, 93.44) and CPL (33.95, 86.26). Local
  (33.5, −21.5) gives board (82.45, 59.44) and CPL (33.95, 120.26).

## Phase 1: Pure core plus unit tests (written test-first)

New section in `kicad_partbench.py` after the Passives block and before the Inbox
section. No tk anywhere in it.

| Function | Purpose |
|---|---|
| `_sexp_block_end(text, start)` | Matcher that skips quoted strings |
| `parse_pad_spec(spec)` → `set[str]` | Ranges for numeric pads, everything else listed explicitly. Rejects reversed or malformed ranges |
| `list_pcb_footprints(pcb_text)` | `[{ref, pad_count, layer}]` for the Parent dropdown |
| `parse_pcb_footprint(pcb_text, ref)` | Position, angle, layer, pads, the aux origin (default 0,0), and outline lines for drawing |
| `subpart_placement(fp, subpart, aux)` | Board X/Y, CPL X/Y and rotation, for both `pads` and `offset` mode |
| `apply_preset(preset)` / `check_preset(fp, preset)` | Fill the sub-parts from a preset, and return a warning for each centre that doesn't match the preset |
| `multipart_presets_load()` / `_save()` | User presets |
| `multipart_config_path` / `_load` / `_save` | Per-project config, with version checking |
| `jlc_output_paths(pro_path)` | Locates the `.kicad_pcb` and `jlcpcb/production_files/{BOM,CPL}-<name>.csv` |
| `patch_cpl(rows, config, placements)` / `patch_bom(rows, config)` | Return `(new_rows, diff)`. Header and the order of all other rows are kept |
| `find_ghosts(pcb_text, config, placements)` | Footprints left on the board that use a sub-part ref, and their distance from the computed position |
| `write_csv_with_backup(path, header, rows)` | Copies to `.bak`, then writes with `csv`, `newline=""` and UTF-8 |

**Tests** (`tests/test_core.py`):

- Pads mode at θ = 0/90/180/270, top and bottom.
- Offset mode at θ = 0/90/180/270, top and bottom.
- A non-square pad on a rotated footprint, to check the bounding-box calculation.
- Pad-spec parser: ranges, lists, non-numeric pads, errors.
- The M-050 Rev-12 fixture with the CM5 preset gives the spec's four numbers and no
  warnings. A shifted copy of the fixture triggers the warning.
- Patching the BOM/CPL twice gives the same result as patching once, including when
  the ghost J6/J7 rows are already there and when the parent ref shares a row with
  other refs.
- A quoted `(` inside a property doesn't break the footprint parser.

## Phase 2: Tab UI

These are module-level `_multipart_*` functions attached to `App`, as the Passives tab
does. Add the tab ` 🔌 Multi-part CPL ` between BOM Manager and Passives.

1. **Project** row: browse to the `.kicad_pro` and show which files were found (✓/✗
   for PCB, BOM and CPL).
2. **Parent** dropdown (high-pad-count footprints first), **Preset** dropdown and
   **Save as preset…**
3. **Sub-parts table**:
   - columns: ref, mode, pads, comment, footprint, LCSC, rotation offset, X/Y offset
   - double-click to edit a row
   - buttons: Add, Remove
4. **Preview canvas** in board coordinates with y pointing down:
   - the outline, and pads coloured by group
   - a crosshair at each sub-part centre, labelled with its CPL X/Y/rotation
   - a pin-1 marker for each group
   - ghost footprints in red, with a line to where they should be
   - it redraws as offsets are typed
   - clicking on the canvas fills X/Y for an offset-mode sub-part
5. **Diff panel**: removed, added and replaced rows, for both the CPL and the BOM.
6. **Buttons**:
   - **Preview**: dry run.
   - **Patch CSVs**: asks for confirmation, writes the `.bak` files, writes the CSVs,
     then runs Preview again.

   Patch CSVs stays disabled until Preview has been run against the CSVs as they are
   on disk now (checked by file mtime).
7. **Save config**.

## Phase 3: GUI feature test

Add `tests/test_features_multipart.py` using `GuiTestCase`. It builds a temp project
(PCB fixture plus plugin-style CSVs), applies the CM5 preset, then runs Preview and
Patch. It checks:

- the CSVs on disk, read with plain `csv` rather than the app's own helpers
- that the `.bak` files exist
- that patching again doesn't change the files

## Phase 4: Docs

- README:
  - a tab section
  - a screenshot at `docs/screenshots/multipart-cpl.png`
  - `.multipart.json`, `multipart_presets.json` and the CSV `.bak` files added to
    "Files PartBench writes"
- CHANGELOG: an entry, marked experimental like the other new tabs (#1).
- Spec: change the status to "in progress" and record the Phase 0 findings.

## Deferred

- Pre-filling from parent footprint fields such as `Field5 = "2x 10164227-1001A1RLF"`.
  The format is free text and the config already covers it.
