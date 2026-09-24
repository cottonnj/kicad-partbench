# Standalone repair tools

Three small tkinter tools for fixing libraries damaged by older imports or hand edits. Each is self-contained (no dependencies, no import of `kicad_partbench.py`) and makes a `.bak` backup before writing anything.

```bash
python tools/symbol_deduplicator.py
python tools/fix_broken_footprints.py
python tools/repair_broken_symbols.py
```

## symbol_deduplicator.py

Loads a `.kicad_sym` file and groups symbols that differ only by an import suffix: `LM358DR`, `LM358DR_imp1`, `LM358DR_1_1`, and so on. It shows which version it will keep in each group (**Auto-Select Newest** changes that), then **Clean Library** removes the rest.

## fix_broken_footprints.py

Some older importers wrote the characters `\n` into `.kicad_mod` files instead of real line breaks, usually around the `(model …)` block:

```
\n  (model "..\3d\step\B2B-XH-A.step")\n)
```

KiCad can't read those files. This tool scans a `.pretty` folder, lists the affected footprints, and replaces the literal `\n` with real newlines.

## repair_broken_symbols.py

Fixes symbols where an `MPN` property was inserted *inside* another property instead of next to it, which breaks the S-expression:

```
(property "Reference" "J"
  (at -12.7 13.462 0)
    (property "MPN" "12345")      <- nested: KiCad refuses the file
  (effects ...
```

The tool moves each nested `MPN` out into its own property.
