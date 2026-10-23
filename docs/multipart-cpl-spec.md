# Spec: Multi-part CPL tab ("one symbol, several placed parts")

Status: implemented in 6.1.x as an experimental tab (see [the plan](multipart-cpl-plan.md)
and "Findings" at the end). Written 2026-10-22 from the VHF-Shark-PCBs session.

## Problem

Some schematic parts are really several physical parts on the board. The main case
is a Raspberry Pi CM5 (or CM4) carrier: the schematic has one 200-pin `Module1`
symbol, but JLCPCB has to place **two** 100-pin board-to-board connectors (J6, J7).

The current workaround is "ghost" J6/J7 symbols kept as DNP until DRC is done.
Then they're placed by hand on top of `Module1`'s pads, the DRC errors are ignored,
and the job is submitted. That's manual, depends on placing the parts exactly
right, and has already gone wrong once. In M-050 Rev-12 the ghost J7 sat at
(33.8, 93.16) instead of (82.45, 93.44), and the BOM listed J7 only, under a
different MPN.

## Goal

A PartBench tab that runs **after** the JLCPCB KiCad plugin (Bouni's
kicad-jlcpcb-tools) has written its BOM/CPL. It patches those two CSVs:

- removes the parent row (`Module1`) from the CPL (and from the BOM if it's there)
- adds one CPL row per sub-part (J6, J7), placed from the parent footprint's real pads
- adds or updates one BOM line for the sub-parts (Comment, `J6,J7`, Footprint, LCSC, qty 2)

The user changes nothing in KiCad. The ghost symbols go away.

## Non-goals

- Changing the KiCad design or the JLC plugin's `project.db`.
- Running inside KiCad. PartBench reads the `.kicad_pcb` text directly (no `pcbnew`).

## Fits PartBench conventions

- Single file, stdlib only, tkinter. New tab follows the Passives pattern:
  module-level `_create_multipart_tab(self, parent)` etc., attached with
  `App._create_multipart_tab = _create_multipart_tab`, plus `notebook.add(...)` in `App.__init__`.
- **All logic in pure functions with no tk dependency**, unit-tested in
  `tests/test_core.py` (stdlib `unittest`). The tab only wires up the UI.
- Back up before writing (`.bak`, as in the rest of PartBench). Dry-run preview first.
- Update README (tab section + screenshot), CHANGELOG.

## Per-project config

Saved next to the `.kicad_pro` as `<project>.multipart.json`, so each rev
directory carries its own copy and it survives copying a rev folder:

```json
{
  "version": 1,
  "parents": [
    {
      "ref": "Module1",
      "drop_parent_from_cpl": true,
      "subparts": [
        {"ref": "J6", "pads": "101-200", "comment": "10164227-1001A1RLF",
         "footprint": "AMPHENOL_10164227-1001A1RLF", "lcsc": "C?????",
         "rotation_offset": 0, "xy_offset_mm": [0, 0]},
        {"ref": "J7", "pads": "1-100", "comment": "10164227-1001A1RLF",
         "footprint": "AMPHENOL_10164227-1001A1RLF", "lcsc": "C?????",
         "rotation_offset": 0, "xy_offset_mm": [0, 0]}
      ]
    }
  ]
}
```

`pads` takes ranges/lists of pad numbers (`"1-100"`, `"1-50,52"`). Pad numbers are
strings in KiCad and can be like `A1` or `EP`, so ranges apply only to numeric pads
and anything else is listed explicitly.

> Which pin range is J6 and which is J7 must come from the M-050 schematic/BOM
> history. The computed mapping is: pads 101–200 → board (82.45, 59.44), which is
> where the hand-placed ghost J6 is today; pads 1–100 → (82.45, 93.44).

## Core functions (pure, testable)

1. `parse_pcb_footprint(pcb_text, ref)` → position `(x, y)`, orientation (deg),
   layer, and a list of pads `{number, local_xy, size, local_rot}`. Pull out the
   `(footprint ...)` block by balanced-paren scanning, as the existing schematic
   parsers do, and don't rely on regex across blocks. Also read
   `(setup (aux_axis_origin X Y))`, default (0, 0).
2. `subpart_placement(fp, pad_set, rotation_offset, xy_offset)` → board `(x, y)`, rotation.
   - **Match the JLC plugin's convention exactly**, so sub-part rows are
     consistent with every other row it writes (see `fabrication.py`:
     `get_position`, `generate_cpl`, `fix_rotation`, `reposition`):
     - Position = centre of the box drawn around the **pads' bounding boxes**
       (the plugin's `get_position`), not the footprint origin. For symmetric
       two-row connectors this equals the mean of the pad centres.
     - Local → board transform (KiCad y-down, angle θ counter-clockwise on screen):
       `X = fx + lx·cosθ + ly·sinθ`, `Y = fy − lx·sinθ + ly·cosθ`.
     - CPL `Mid X = ToMM(X − auxX)`, `Mid Y = −ToMM(Y − auxY)`.
     - Rotation = footprint orientation; for bottom side `(180 − rot) % 360`;
       then `+ rotation_offset`, mod 360.
     - Bottom-side mirroring of pad X (pads on B.Cu are already stored
       flipped in the file; verify with a bottom-side test fixture).
3. `patch_cpl(rows, config, placements)` / `patch_bom(rows, config)`. **Must be
   safe to re-run**: if a sub-part ref is already present, replace that row
   instead of adding a duplicate. Drop the parent row. Keep the header and the
   order of all other rows. Write with `csv` and `newline=""`, UTF-8 (match the plugin).
4. `find_ghosts(pcb_text, config)`: report any footprint on the board whose
   reference matches a sub-part (left-over ghost parts) and how far it is from
   the computed position.

## Tab UI

- **Project**: pick a `.kicad_pro`. It finds `<name>.kicad_pcb` and
  `jlcpcb/production_files/{BOM,CPL}-<name>.csv`, and loads or creates `<project>.multipart.json`.
- **Parent**: a dropdown of board footprints (show those with ≥ 2× the usual pad
  count first). **Sub-parts table**: ref, pad range, comment, footprint, LCSC,
  rotation offset. "Auto-split" suggests pad groups by clustering pad positions
  (two clear clusters for the CM5).
- **Preview canvas** (the main safety feature):
  - parent courtyard/outline and all pads, coloured by sub-part group
  - computed centre of each sub-part as a crosshair with its CPL X/Y/rot
  - any ghost footprints from `find_ghosts` drawn in red, with the distance
  - pin-1 marker per group, so the rotation can be checked visually
- **Diff panel**: CPL/BOM rows before and after (removed / added / replaced).
- Buttons: **Preview** (dry run), **Patch CSVs** (writes `.bak` first), **Save config**.

## Tests (tests/test_core.py)

- Transform: synthetic footprint at θ = 0/90/180/270, top and bottom; known answers.
- Pad-range parser: ranges, lists, non-numeric pads.
- Patch is safe to re-run: patching twice gives the same result as patching once.
- Real fixture from M-050 Rev-12 (small trimmed `.kicad_pcb` excerpt with only
  `setup` and the `Module1` block). `Module1` is at (103.95, 92.94), θ = 90,
  aux origin (48.5, 179.7), and the pad groups sit at local (−0.5, −21.5) and
  (33.5, −21.5). Expected:
  - pads 101–200 → board (82.45, 59.44) → CPL (33.95, 120.26), rot 90
  - pads 1–100   → board (82.45, 93.44) → CPL (33.95, 86.26), rot 90
  (The existing as-ordered CPL row `J7 … 33.999911, 86.101148` is close but came
  from a hand-placed ghost. Use computed values as truth.)

## Open questions for the user

1. The connector MPN/LCSC actually being ordered: Amphenol 10164227-1001A1RLF
   (in `Module1`'s fields and `Manual edits.txt`) or GT-B0403FSV14-100B1101
   (C42376244, in the current exported BOM)?
2. The JLC rotation for this connector: confirm once in JLC's placement preview,
   then store it as `rotation_offset` in the config.
3. Should the tab also read sub-part info from parent footprint properties (for example the
   existing `Field5 = "2x 10164227-1001A1RLF"`) to pre-fill the table?

## Findings (2026-10-23)

- **Plugin conventions** (kicad-jlcpcb-tools `fabrication.py`, KiCad 10 install):
  - CPL header: `Designator,Val,Package,Mid X,Mid Y,Rotation,Layer`
  - BOM header: `Comment,Designator,Footprint,LCSC,Quantity`. There is a Quantity column.
  - Rows are written with `csv`, `newline=""` and UTF-8.
  - CPL rows are sorted by reference. Numbers are `ToMM` floats (up to 6 decimals),
    rotation is a float such as `90.0`, and the layer is `top` or `bottom`.
  - Position is the centre of the merged pad bounding boxes, **NPTH holes included**.
  - Rotation corrections are added after the bottom-side mirror.
  - The plugin can also shift positions per footprint pattern. Sub-parts don't get
    this; they use their own offsets instead.
- **Pad angles in the board file are absolute.** They already include the footprint
  angle. The Rev-12 `Module1` SMD pads have no angle while the footprint is at 90°.
- **Bottom side:** pad coordinates for footprints on B.Cu are stored already flipped,
  so the same local→board transform works on both sides.
- **Checked against real boards:** the position code reproduces the plugin's CPL on
  618 of 623 rows across five boards, including 50 bottom-side parts. The other
  five rows are explained:
  - four come from CPLs exported before the board was last edited
  - one has a plugin position correction
- **Rev-12 result:** with pads 101–200 → J6 and 1–100 → J7, the computed rows are
  J6 (33.95, 120.26) and J7 (33.95, 86.26). The hand-placed ghost J6 is exactly in
  the right place; the ghost J7 is 48.65 mm off.
- **Q1 (part):** the Amphenol 10164227-1001A1RLF (LCSC C6782225) is used. The
  G-Switch GT-B0403FSV14-100B1101 (C42376244) has the same listed pins, pitch and
  current, and is a pin-compatible alternative.
- **Q2 (rotation):** in the Rev-4 order that JLC accepted, both connectors are at
  0.0 with Module1 at 90, so the CM5 preset uses `rotation_offset = -90`. That order
  used the G-Switch part. Each LCSC part has its own JLC footprint, so check the
  rotation once in JLC's placement preview with the Amphenol part.
- **Q3 (Field5 pre-fill):** deferred.
