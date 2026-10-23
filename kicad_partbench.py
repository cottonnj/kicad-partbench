#!/usr/bin/env python3
"""
KiCad PartBench v6.1  (formerly "KiCad Library Tool")
═══════════════════════════════════════════════════════════════════════════════
Modern, portable tool for managing KiCad component libraries.

• Import symbols, footprints, 3D models from ZIP/directories
• Intelligent duplicate detection (content-based hashing)
• Automatic 3D model pairing  
• 3D Models tab — validate, locate, and repair model paths across the library
• BOM Manager — sync Octopart BOMs to schematics
• Passives tab — library-driven MPN/LCSC assignment for R/C/L
• Library management tools
• Modern dark theme, no external dependencies

Version: 6.1.0 | Date: 2026-09-22 | License: MIT

Changes since 6.0:
  • NEW : "📥 Inbox" tab (first tab). Two side-by-side lists: your
          browser Downloads folder (zips only by default, newest first,
          files from the last 15 min highlighted) and the zips folder
          (= the Import tab's Source directory, with an Imported/New
          status column read from the import log). Move → (or double-
          click) moves selected downloads into zips; ↩ moves a zip back;
          🗑 sends a wrong download to the Recycle Bin. Both lists
          auto-refresh every 2 s while the tab is visible. 📂 buttons open
          either folder in Explorer/Finder.
  • NEW : "Import Zips" on the Inbox tab runs the normal import with the
          Import-tab options, but stays on the Inbox tab (no popup) and
          lists every symbol from the run -- including ones that were
          already in the library -- with its footprint. Double-click or
          Ctrl+C copies the symbol name; right-click / buttons copy
          Lib:Symbol, the footprint, or all names. "Full Summary…"
          opens the usual detailed dialog on demand.
  • NEW : Symbol-library nickname detection from sym-lib-table (used for
          the Lib:Symbol copy), mirroring the footprint nickname logic.
  • FIX : Tab-change handler used hard-coded tab indices; now matches by
          label so inserting tabs can't break Library/3D auto-refresh.
  • Prefs: Downloads folder and "zips only" filter are remembered.

Changes since 5.8:
  • NEW : Package field is now a combobox with auto-complete. Drops down
          to show the standard KiCad SMD package strings for the current
          part type (R_0603_1608Metric etc.) plus every package string
          already used in the library. Editable — type any custom package
          name not in the list. Custom names you save auto-populate next
          time you open a form.

Changes since 5.7:
  • NEW : Passives tab — "Save All Complete to Library" walkthrough.
          Scans the table, finds every unique (type, value, package, MPN)
          combination that has Value+Footprint+MPN populated, dedups
          against the existing library, then walks you through each one
          in a pre-filled form. Required fields are just Value/Package/MPN
          — LCSC and all spec fields are optional. Buttons: Save & Next,
          Skip, Save All Remaining (minimal), Cancel.
  • NEW : Status filter in the Passives table — All / Missing MPN /
          Missing LCSC / Missing MPN or LCSC / Has MPN and LCSC /
          Missing Footprint. Intersects with Type and text filters.
  • UI  : "💾 Save to Library" moved out of the Manual Entry section
          into its own dedicated section so it's actually findable.

Changes since 5.6:
  • NEW : "Passives" tab — automate the per-component MPN/LCSC chore for
          resistors, capacitors, and inductors. Scans the whole project
          (hierarchical sheets included), groups passives by normalized
          (Value, Footprint), and applies MPN+LCSC+supporting fields
          (Tolerance, Voltage, Dielectric, Power, Isat, etc.) to every
          checked instance in one click.
  • NEW : Global passives library at ~/.kicad-library-tool/passives.json.
          Saved entries carry full spec metadata (voltage, dielectric,
          tolerance, power, Isat, DCR, core, etc.) so the dropdown can
          disambiguate two parts that share Value+Footprint but differ in
          the underlying spec (e.g. 1uF 0603 16V X7R vs 1uF 0603 50V C0G).
  • NEW : Library Manager modal (Add / Edit / Remove / Import / Export).
  • NEW : Unit normalizer — uF/UF/uf/µF all match; k/K folded; m (milli)
          and M (mega) are kept case-distinct; old-style 4k7 → 4.7k.
  • NEW : Heterogeneous-selection block — can't accidentally apply one
          MPN to a mix of different values. Edit Row… escape hatch for
          forcibly changing any field on a single component.
  • NEW : Pre-write overwrite confirmation showing every existing value
          that would be replaced, in one batch dialog.

Changes since 5.5:
  • FIX : Imported footprints had Windows backslashes left in 3D model paths
          because the path-normalization replaced "\\\\" (two backslashes)
          instead of "\\" (one). KiCad's parser then read "\\3" as octal
          escape chr(3), so paths displayed as "..d\\step\\..." in the viewer.
          Now correctly converts single backslashes to forward slashes.
  • NEW : "3D Models" tab — scans every footprint, validates 3D paths, finds
          missing models in the library directory (exact + fuzzy match with
          confidence), reports backslash corruption (without modifying any
          file during scan), and offers an integrated manual search.
  • NEW : "Fix Corrupted Paths…" preview dialog — shows current vs proposed
          path for every affected footprint, lets the user untick any row
          to skip, fixes ticked rows on confirm, then shows a summary list.
  • NEW : Import skip-list — packages already imported are remembered in
          a per-library JSON log (".kicad_personal_import_log.json") and
          skipped on next import. Vastly speeds up incremental imports
          (add 1 part to a folder of 50, only that 1 is processed).
          Tools → View / Reset Import Log… to inspect or clear.
  • NEW : Detailed import summary popup — lists actual symbol/footprint/
          model names that were imported, not just totals.
  • FIX : Manual 3D assign dialog crash (scrolledtext.ScrolledText name).
  • FIX : "Switch to Results tab" used a hard-coded index that pointed to
          the wrong tab after the new 3D Models tab was inserted; now
          looks up the tab by label.
═══════════════════════════════════════════════════════════════════════════════
"""
import os
import re
import sys
import json
import math
import time
import shutil
import hashlib
import tempfile
import zipfile
import textwrap
import csv
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

HEADER = '(kicad_symbol_lib (version 20211014) (generator "Kicad_Library_Tool_4.6")\n'
NONESSENTIAL_PROPS = {"Datasheet","Description","ki_description","ki_keywords","ki_locked","Comment","Keywords",
                      "LCSC","LCSC Part","MPN","MFR","Manufacturer","Supplier","Supplier Part","Part Number",
                      "Link","URL","Alt","Notes"}
PREFS_FILE = Path.home() / ".kicad_personal_importer.json"

APP_NAME = "KiCad PartBench"
APP_VERSION = "6.1.0"


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT LOG  (skip-list to speed up incremental imports)
# ═══════════════════════════════════════════════════════════════════════════
#
# Stored as a JSON file inside the personal library directory:
#     <personal>/.kicad_personal_import_log.json
#
# Per-package fingerprint:
#     zip  → "zip|<resolved-lower>|<size>|<int(mtime)>"
#     dir  → "dir|<resolved-lower>|<int(mtime)>"
#
# A zip swapped on disk (different size or mtime) gets a new fingerprint and
# is therefore re-imported. A directory whose top-level mtime hasn't changed
# is considered already-imported. (Directories whose mtime is stale because
# only nested files changed will be skipped — there's a "Reset Import Log"
# escape hatch in the Tools menu for that case.)
class ImportLog:
    FILENAME = ".kicad_personal_import_log.json"

    def __init__(self, personal_root: Path):
        self.personal_root = Path(personal_root) if personal_root else None
        self.path = (self.personal_root / self.FILENAME) if self.personal_root else None
        self._data = {"version": 1, "packages": {}}
        self._load()

    # ---- IO ----
    def _load(self):
        if self.path and self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    self._data = d
                    self._data.setdefault("version", 1)
                    self._data.setdefault("packages", {})
            except Exception:
                pass

    def save(self):
        if not self.path:
            return
        try:
            self.path.write_text(json.dumps(self._data, indent=2),
                                 encoding="utf-8")
        except Exception:
            pass

    # ---- Fingerprints ----
    @staticmethod
    def fingerprint(kind: str, path: Path) -> str:
        try:
            rp = str(Path(path).resolve()).lower()
        except Exception:
            rp = str(path).lower()
        try:
            stat = path.stat()
        except Exception:
            return f"{kind}|{rp}|0|0"
        if kind == "zip":
            return f"zip|{rp}|{stat.st_size}|{int(stat.st_mtime)}"
        return f"dir|{rp}|{int(stat.st_mtime)}"

    # ---- Queries ----
    def contains(self, kind: str, path: Path) -> bool:
        return self.fingerprint(kind, path) in self._data["packages"]

    def count(self) -> int:
        return len(self._data.get("packages", {}))

    def list_entries(self):
        """Yield (fingerprint_key, dict) for each remembered package."""
        for k, v in self._data.get("packages", {}).items():
            yield k, v

    # ---- Mutations ----
    def remember(self, kind: str, path: Path, summary: dict = None):
        fp = self.fingerprint(kind, path)
        entry = {
            "kind": kind,
            "path": str(path),
            "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            stat = path.stat()
            entry["mtime"] = stat.st_mtime
            if kind == "zip":
                entry["size"] = stat.st_size
        except Exception:
            pass
        if summary:
            entry["summary"] = {
                k: summary.get(k, 0)
                for k in ("symbols_added", "symbols_renamed", "symbols_overwritten",
                          "footprints_imported", "models_copied", "models_paired")
            }
        self._data["packages"][fp] = entry

    def forget(self, kind: str, path: Path) -> bool:
        fp = self.fingerprint(kind, path)
        if fp in self._data["packages"]:
            del self._data["packages"][fp]
            return True
        return False

    def reset(self):
        self._data = {"version": 1, "packages": {}}


def ensure_paths(personal_dir: Path, sym_name: str, fp_folder: str):
    (personal_dir / fp_folder).mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "step").mkdir(parents=True, exist_ok=True)
    (personal_dir / "3d" / "wrl").mkdir(parents=True, exist_ok=True)
    sym_path = personal_dir / sym_name
    if not sym_path.exists():
        sym_path.write_text(HEADER + ")\n", encoding="utf-8")
    return sym_path

def find_files(root: Path, patterns):
    out = []
    for pat in patterns:
        out += list(root.rglob(pat))
    return out

def package_has_content(p: Path):
    if p.is_file() and p.suffix.lower() == ".zip": return True
    if not p.is_dir(): return False
    for pat in ("*.kicad_sym","*.kicad_mod","*.step","*.stp","*.wrl"):
        if list(p.glob(pat)): return True
    for sub in ("KiCADv6","kicad6","KiCad","Library.pretty","footprints.pretty"):
        if (p / sub).exists(): return True
    return False

def gather_packages(source: Path, recurse=True):
    pkgs = []
    if source.is_file() and source.suffix.lower() == ".zip":
        pkgs.append(("zip", source)); return pkgs
    if source.is_dir():
        if package_has_content(source): pkgs.append(("dir", source))
        if recurse:
            for p in source.rglob("*"):
                if p.is_file() and p.suffix.lower() == ".zip":
                    pkgs.append(("zip", p))
                elif p.is_dir() and package_has_content(p):
                    pkgs.append(("dir", p))
    uniq, seen = [], set()
    for kind, p in pkgs:
        key = (kind, str(p.resolve()))
        if key not in seen:
            seen.add(key); uniq.append((kind, p))
    return uniq

def prune_packages(pkgs, prefer="folder"):
    abs_pkgs = [(k, Path(p).resolve()) for k, p in pkgs]
    parent_dirs = set()
    for i, (ki, pi) in enumerate(abs_pkgs):
        if ki != "dir": continue
        for j, (kj, pj) in enumerate(abs_pkgs):
            if i != j and str(pj).startswith(str(pi) + os.sep):
                parent_dirs.add(pi); break
    leaf = [(k,p) for (k,p) in abs_pkgs if not (k == "dir" and p in parent_dirs)]
    grouped = {}
    for kind, p in leaf:
        parent = p.parent; stem = p.stem if kind == "zip" else p.name
        grouped.setdefault((str(parent), stem.lower()), []).append((kind, p))
    pruned = []
    for _, items in grouped.items():
        if len(items) == 1:
            pruned.extend(items); continue
        kinds = {k for k,_ in items}
        if "zip" in kinds and "dir" in kinds:
            chosen = [it for it in items if it[0] == ("zip" if prefer=="zip" else "dir")][0]
            pruned.append(chosen)
        else:
            latest = max(items, key=lambda t: t[1].stat().st_mtime if t[1].exists() else 0)
            pruned.append(latest)
    return pruned

# --------------- 3D helpers ---------------
def _canon(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())

def _score(a: str, b: str) -> float:
    if not a or not b: return 0.0
    if a == b: return 1.0
    def grams(t):
        return {t[i:i+4] for i in range(max(1, len(t)-3))}
    ga, gb = grams(a), grams(b); inter = len(ga & gb); union = len(ga | gb) or 1
    base = inter/union
    bonus = 0.1 if a[:6]==b[:6] else 0.0
    bonus += 0.1 if a[-6:]==b[-6:] else 0.0
    return min(1.0, base+bonus)

def _available_models_index(models_root: Path):
    exact, stems, canon, all_files = {}, {}, {}, []
    if models_root.exists():
        for f in models_root.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".step",".stp",".wrl"):
                exact[f.name.lower()] = f
                stems.setdefault(f.stem.lower(), []).append(f)
                canon.setdefault(_canon(f.stem), []).append(f)
                all_files.append(f)
    return exact, stems, canon, all_files

def _prefer_ext(files_with_same_stem: list, prefer: str):
    order = [".step",".stp",".wrl"] if prefer == "step" else [".wrl",".step",".stp"]
    for ext in order:
        for f in files_with_same_stem:
            if f.suffix.lower() == ext: return f
    return files_with_same_stem[0] if files_with_same_stem else None

def _choose_local_model_smart(all_idx, key_name: str, prefer: str="step"):
    exact, stems, canon, files = all_idx
    base = Path(key_name.replace("\\","/")).name.lower()
    if base in exact: return exact[base]
    st = Path(base).stem.lower()
    if st in stems: return _prefer_ext(stems[st], prefer)
    c = _canon(st)
    if c in canon: return _prefer_ext(canon[c], prefer)
    best = (None, 0.0)
    for f in files:
        sc = _score(_canon(f.stem), c)
        if sc > best[1]: best = (f, sc)
    return best[0] if best[1] >= 0.62 else None

def _rewrite_all_models(txt: str, chooser, make_rel=None):
    """Rewrite model paths in footprint text, preserving transform data."""
    result = []
    pos = 0
    
    while True:
        # Find next model block
        match = re.search(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt[pos:])
        if not match:
            result.append(txt[pos:])
            break
        
        # Append text before model
        result.append(txt[pos:pos + match.start()])
        
        # Find complete model block by counting parentheses
        model_start = pos + match.start()
        paren_count = 0
        i = model_start
        
        while i < len(txt):
            if txt[i] == '(':
                paren_count += 1
            elif txt[i] == ')':
                paren_count -= 1
                if paren_count == 0:
                    i += 1  # Include closing paren
                    break
            i += 1
        
        model_block = txt[model_start:i]
        path = match.group(1) or match.group(2)
        
        # Get new model path
        local = chooser(path)
        if local:
            if make_rel:
                new_path = make_rel(local)
            else:
                sub = "step" if local.suffix.lower() in (".step",".stp") else "wrl"
                new_path = f"${{PERSONAL_LIB_DIR}}/3d/{sub}/{local.name}"
            
            # Replace just the path, keeping all transform data
            new_block = re.sub(
                r'(\(model\s+)(?:"[^"]+"|[^\s)]+)',
                rf'\1"{new_path}"',
                model_block,
                count=1
            )
            result.append(new_block)
        else:
            result.append(model_block)
        
        pos = i
    
    return ''.join(result)

# --------------- Symbols ---------------
def parse_symbol_blocks(text: str):
    blocks = []
    i, n = 0, len(text)
    while True:
        i = text.find("(symbol ", i)
        if i == -1: break
        mname = re.search(r'\(symbol\s+"([^"]+)"', text[i:])
        if not mname: break
        name = mname.group(1)
        depth, j = 0, i
        while j < n:
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    j += 1; break
            j += 1
        block = text[i:j]
        blocks.append((name, block))
        i = j
    return blocks

def sanitize_block(parent_name: str, block: str) -> str:
    s = textwrap.dedent(block.strip())
    k = s.find("(symbol ");  s = s[k:] if k >= 0 else s
    s = re.sub(r'\(hide\)', '(hide yes)', s)
    # unit name normalization (keeps parent as prefix)
    matches = list(re.finditer(r'\(symbol\s+"([^"]+)"', s))
    if len(matches) > 1:
        for m in reversed(matches[1:]):
            child = m.group(1)
            mu = re.match(r'^(.*)_(\d+)_(\d+)$', child)
            if mu:
                u, c = max(1,int(mu.group(2))), max(1,int(mu.group(3)))
                new_name = f'{parent_name}_{u}_{c}'
            else:
                new_name = f'{parent_name}_1_1'
            s = s[:m.start(1)] + new_name + s[m.end(1):]
    lines = []
    for ln in s.splitlines():
        lines.append("  " + ln.lstrip().replace("\t", "  "))
    return "\n".join(lines)

def strip_footprint_prop(block: str):
    return re.sub(r'\(\s*property\s+"Footprint"\s+"[^"]*"\s*\(.*?\)\s*\)', '', block, flags=re.DOTALL)

def strip_prop_ids(block: str):
    s = re.sub(r'\(id\s+\d+\)', '', block)
    s = re.sub(r'\(uuid\s+[^)]+\)', '', s)
    s = re.sub(r'\(at\s+[^)]+\)', '', s)
    s = re.sub(r'\(effects\s+\(.*?\)\)', '', s, flags=re.DOTALL)
    return s

def drop_nonessential_props(block: str):
    def repl(m):
        name = m.group(1)
        if name in NONESSENTIAL_PROPS: return ''
        return f'(property "{name}" "{m.group(2)}")'
    return re.sub(r'\(\s*property\s+"([^"]+)"\s+"([^"]*)".*?\)', repl, block, flags=re.DOTALL)

def canonical_content(block: str, mode: str, ignore_footprint=True) -> str:
    s = re.sub(r'\s+', ' ', block).strip()
    if mode == "Exact": return s
    s = strip_prop_ids(s)
    if ignore_footprint: s = strip_footprint_prop(s)
    s = drop_nonessential_props(s)
    s = re.sub(r'\s+', ' ', s).strip()
    if mode == "Pins-only":
        pins = []
        for m in re.finditer(r'\(pin\s+([a-zA-Z0-9_]+)\s+[a-zA-Z0-9_]+\s*\(.*?\(number\s+"([^"]+)"\).*?\(name\s+"([^"]*)"\)', s, flags=re.DOTALL):
            pins.append((m.group(1), m.group(2), m.group(3)))
        pins.sort()
        return json.dumps(pins, separators=(',',':'))
    return s

def read_personal_symbols(sym_path: Path, mode: str, ignore_footprint=True):
    if not sym_path.exists(): return {}, {}
    raw = sym_path.read_text(encoding="utf-8", errors="ignore")
    merged = {}; content_map = {}
    for name, block in parse_symbol_blocks(raw):
        sanitized = sanitize_block(name, block)
        canon = canonical_content(sanitized, mode, ignore_footprint=ignore_footprint)
        merged[name] = sanitized
        content_map[hashlib.sha1(canon.encode("utf-8")).hexdigest()] = name
    return merged, content_map

def write_symbols_file(sym_path: Path, blocks_by_name: dict):
    out = [HEADER]
    for name in sorted(blocks_by_name.keys(), key=lambda s: s.lower()):
        out.append(blocks_by_name[name].rstrip() + "\n")
    out.append(")\n")
    backup = sym_path.with_suffix(".kicad_sym.bak")
    if sym_path.exists():
        try: sym_path.rename(backup)
        except Exception: shutil.copy2(sym_path, backup)
    Path(sym_path).write_text("".join(out), encoding="utf-8")
    return str(backup)

# --------------- Import copy helpers ---------------
def copy_3d_models(staging: Path, personal: Path, overwrite=False, log=lambda *a: None):
    """Copy STEP/WRL files from staging into the personal library.

    Creates ``personal/3d/step`` and ``personal/3d/wrl`` if missing so this
    function works standalone (the import flow's ``ensure_paths`` does this
    too, but other callers — e.g. the 3D Models tab tests — don't).

    Returns (mapping, copied_count, copied_names).
        mapping       : {filename → "${PERSONAL_LIB_DIR}/3d/<sub>/<filename>"}
        copied_count  : int — how many files were actually copied to disk
        copied_names  : list[str] — filenames that were newly copied this run
    """
    steps = find_files(staging, ["*.step", "*.stp"])
    wrls  = find_files(staging, ["*.wrl"])
    step_dir = personal / "3d" / "step"
    wrl_dir  = personal / "3d" / "wrl"
    if steps:
        step_dir.mkdir(parents=True, exist_ok=True)
    if wrls:
        wrl_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}; copied = 0
    copied_names = []
    for f in steps:
        dest = step_dir / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1; log(f"[3D] Copied: {f.name}")
            copied_names.append(f.name)
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/step/" + f.name
    for f in wrls:
        dest = wrl_dir / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1; log(f"[3D] Copied: {f.name}")
            copied_names.append(f.name)
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/wrl/" + f.name
    return mapping, copied, copied_names

def import_footprints(staging: Path, personal: Path, fp_folder: str, overwrite=False, log=lambda *a: None):
    mods = find_files(staging, ["*.kicad_mod"])
    imported, skipped, errors = [], [], []
    out_paths = []
    for src in mods:
        dest = personal / fp_folder / src.name
        try:
            if dest.exists() and not overwrite:
                skipped.append(src.name); log(f"[FP] Skip (exists): {src.name}")
            else:
                shutil.copy2(src, dest); imported.append(src.name); log(f"[FP] Imported: {src.name}")
            out_paths.append(dest)
        except Exception as e:
            errors.append(f"{src.name}: {e}"); log(f"[FP] ERROR {src.name}: {e}")
    return out_paths, imported, skipped, errors

# --------------- Symbol merge ---------------
def merge_symbols_strict(personal_sym: Path, vendor_syms: list, imported_fp_paths: list,
                         autoset_fp=True, fp_nick="Personal_Footprints", overwrite=False,
                         mode="Ignore Properties", ignore_footprint_for_dedupe=True, 
                         duplicate_mode="skip", log=lambda *a: None):
    """
    Merge symbols with improved duplicate handling.
    
    Args:
        duplicate_mode: "skip" = skip duplicates (default), 
                       "replace" = replace existing,
                       "rename" = add _imp suffix
    """
    existing, content_index = read_personal_symbols(personal_sym, mode, ignore_footprint=ignore_footprint_for_dedupe)
    merged = dict(existing)

    fp_names = []
    for p in imported_fp_paths:
        try:
            txt = Path(p).read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'\(footprint\s+"([^"]+)"', txt)
            fp_names.append(m.group(1) if m else Path(p).stem)
        except Exception:
            fp_names.append(Path(p).stem)
    uniq = {n for n in fp_names if n}
    single_fp = next(iter(uniq)) if len(uniq) == 1 else None

    stats = {"added":[], "renamed":[], "overwritten":[], "skipped_content_dupe":[], "skipped_exact":[], "debug_symbols":[]}

    for symlib in vendor_syms:
        txt = Path(symlib).read_text(encoding="utf-8", errors="ignore")
        for name, raw_block in parse_symbol_blocks(txt):
            block = textwrap.dedent(raw_block.strip())
            if autoset_fp and single_fp:
                fpfull = f"{fp_nick}:{single_fp}"
                if re.search(r'\(property\s+"Footprint"\s+"[^"]*"', block):
                    block = re.sub(r'(\(property\s+"Footprint"\s+)"[^"]*"', r'\1"{}"'.format(fpfull), block, count=1)
                else:
                    if block.endswith(")"):
                        block = block[:-1] + f'\n    (property "Footprint" "{fpfull}" (at 0 0 0) (effects (font (size 1 1))) (hide yes))\n)\n'
            sanitized = sanitize_block(name, block)
            canon = canonical_content(sanitized, mode, ignore_footprint=ignore_footprint_for_dedupe)
            h = hashlib.sha1(canon.encode("utf-8")).hexdigest()

            # Skip if content is identical
            if h in content_index:
                kept = content_index[h]
                stats["skipped_content_dupe"].append((name, kept))
                stats["debug_symbols"].append((name, "SKIP_DUP", kept))
                log(f"[SYM] Skip duplicate by content: {name} == {kept}")
                continue

            # Handle name collision
            if name in merged:
                if overwrite or duplicate_mode == "replace":
                    # Replace existing symbol
                    merged[name] = sanitized
                    content_index[h] = name
                    stats["overwritten"].append(name)
                    stats["debug_symbols"].append((name, "REPLACE", name))
                    log(f"[SYM] Replaced: {name}")
                elif duplicate_mode == "rename":
                    # Add with _imp suffix
                    k, new_name = 1, f"{name}_imp1"
                    while new_name in merged:
                        k += 1
                        new_name = f"{name}_imp{k}"
                    # Replace ALL occurrences of the symbol name (parent and child units)
                    def rename_symbol(match):
                        old_sym_name = match.group(1)
                        if old_sym_name == name:
                            return f'(symbol "{new_name}"'
                        elif old_sym_name.startswith(name + "_"):
                            suffix = old_sym_name[len(name):]
                            return f'(symbol "{new_name}{suffix}"'
                        return match.group(0)
                    sanitized = re.sub(r'\(symbol\s+"([^"]+)"', rename_symbol, sanitized)
                    merged[new_name] = sanitized
                    content_index[h] = new_name
                    stats["renamed"].append((name, new_name))
                    stats["debug_symbols"].append((name, "RENAME", new_name))
                    log(f"[SYM] Renamed: {new_name}")
                else:
                    # Default: skip (don't import)
                    stats["skipped_exact"].append(name)
                    stats["debug_symbols"].append((name, "SKIP_EXISTS", name))
                    log(f"[SYM] Skip (exists): {name}")
            else:
                # New symbol, add it
                merged[name] = sanitized
                content_index[h] = name
                stats["added"].append(name)
                stats["debug_symbols"].append((name, "ADD", name))
                log(f"[SYM] Added: {name}")

    return stats, merged

# --------------- ZIP staging ---------------
def unzip_if_needed(path: Path):
    if path.is_dir(): return path, None
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="kicad_import_"))
        with zipfile.ZipFile(path, "r") as z: z.extractall(tmp)
        return tmp, tmp
    raise FileNotFoundError("Source is neither a directory nor a .zip")

# --------------- Import-time 3D pairing ---------------
def _symbol_candidates_from_files(symbol_files):
    cands = set()
    for symlib in symbol_files:
        txt = Path(symlib).read_text(encoding="utf-8", errors="ignore")
        for name, block in parse_symbol_blocks(txt):
            cands.add(name)
            m = re.search(r'\(\s*property\s+"Value"\s+"([^"]*)"', block)
            if m and m.group(1): cands.add(m.group(1))
            m = re.search(r'\(\s*property\s+"MPN"\s+"([^"]*)"', block)
            if m and m.group(1): cands.add(m.group(1))
    return {c for c in cands if c and len(c) >= 3}

def _choose_from_models(models, key, prefer="step"):
    # choose best model Path from list for a key string
    if not models: return None
    keyc = _canon(Path(key).stem)
    best = (None, 0.0)
    for f in models:
        sc = _score(_canon(f.stem), keyc)
        if sc > best[1]: best = (f, sc)
    if best[0] and best[1] >= 0.62:
        # prefer extension
        same_stem = [m for m in models if _canon(m.stem) == _canon(best[0].stem)]
        if same_stem:
            return _prefer_ext(same_stem, prefer)
        return best[0]
    return None

def _write_model_into_footprint(mod_path: Path, model_path: Path, relative_to_dir: Path, use_relative: bool):

    txt = mod_path.read_text(encoding="utf-8", errors="ignore")
    personal_root = mod_path.parent.parent  # parent of *.pretty is personal root

    # Build the path we will write
    if use_relative:
        try:
            # NOTE: replace single backslash with forward slash. Previously
            # this used "\\\\" which in a Python string literal is two
            # backslashes -- so single backslashes from os.path.relpath
            # survived into the .kicad_mod file and were then interpreted
            # as escape sequences by KiCad's S-expression parser
            # (e.g. \3 -> chr(3)), producing junk paths like "..d\step\...".
            rel = os.path.relpath(model_path, start=relative_to_dir).replace("\\","/")
        except Exception:
            rel = model_path.name
        new_model_str = f'(model "{rel}")'
    else:
        sub = "step" if model_path.suffix.lower() in (".step",".stp") else "wrl"
        rel = f'${{PERSONAL_LIB_DIR}}/3d/{sub}/{model_path.name}'
        new_model_str = f'(model "{rel}")'

    # Check if an equivalent model is already present (compare by filename, case-insensitive)
    existing = [mm.group(1) or mm.group(2) for mm in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]
    new_key = _norm_model_key(rel, relative_to_dir, personal_root)
    for pstr in existing:
        if _norm_model_key(pstr, relative_to_dir, personal_root) == new_key:
            cleaned = _dedupe_models_in_text(txt, relative_to_dir, personal_root)
            if cleaned != txt:
                mod_path.write_text(cleaned, encoding="utf-8")
            return

    # Append and then dedupe as a safety net
    if txt.rstrip().endswith(')'):
        txt2 = txt.rstrip()[:-1] + f"\n  {new_model_str}\n)"
    else:
        txt2 = txt + f"\n  {new_model_str}\n"
    txt2 = _dedupe_models_in_text(txt2, relative_to_dir, personal_root)
    mod_path.write_text(txt2, encoding="utf-8")


def process_one_package(kind: str, path: Path, personal: Path, fp_folder: str, personal_sym: Path,
                        fp_nick="Personal_Footprints", autoset_fp=True, overwrite=False, mode="Ignore Properties",
                        ignore_footprint_for_dedupe=True, duplicate_mode="skip", dry_run=False, log=lambda *a: None,
                        import_pair_models=True, prefer_model_ext="step", force_relative_models=False):
    res = {
        "package": str(path), "kind": kind,
        "footprints_imported": 0, "footprints_skipped": 0, "footprints_errors": 0,
        "symbols_added": 0, "symbols_renamed": 0, "symbols_overwritten": 0,
        "symbols_skipped_content_dupe": 0, "symbols_skipped_exact": 0, "models_copied": 0, "errors": [],
        "symbol_names_debug": [], "models_paired": 0,
        # Detailed name lists -- consumed by the post-import summary dialog
        # so the user can see exactly what came from this package.
        "symbols_added_names": [],
        "symbols_renamed_pairs": [],   # list[tuple[old_name, new_name]]
        "symbols_overwritten_names": [],
        "footprints_added_names": [],
        "models_copied_names": [],
        "models_paired_pairs": [],     # list[tuple[footprint_name, model_name]]
    }
    try:
        if kind == "zip": staging, tmpdir = unzip_if_needed(path)
        else: staging, tmpdir = (path, None)

        # copy 3D + footprints
        model_map, copied3d, copied_names = copy_3d_models(staging, personal, overwrite=overwrite, log=log)
        res["models_copied"] = copied3d
        res["models_copied_names"] = copied_names
        fps_paths, imported, skipped, fp_errs = import_footprints(staging, personal, fp_folder, overwrite=overwrite, log=log)
        res["footprints_imported"] = len(imported); res["footprints_skipped"] = len(skipped); res["footprints_errors"] = len(fp_errs)
        res["footprints_added_names"] = list(imported)

        # Import-time 3D pairing
        if import_pair_models and not dry_run and fps_paths:
            # Collect models present in staging
            staged_models = [*find_files(staging, ["*.step","*.stp","*.wrl"])]
            # Prefer within the same zip/folder
            if staged_models:
                # Candidates from symbols
                sym_files = find_files(staging, ["*.kicad_sym"])
                sym_cands = _symbol_candidates_from_files(sym_files)
                # One-to-one fast path
                if len(fps_paths) == 1 and len(staged_models) == 1:
                    chosen_model = personal / ("3d/step" if staged_models[0].suffix.lower() in (".step",".stp") else "3d/wrl") / staged_models[0].name
                    _write_model_into_footprint(fps_paths[0], chosen_model,
                                                personal / fp_folder, force_relative_models)
                    res["models_paired"] += 1
                    res["models_paired_pairs"].append((Path(fps_paths[0]).name, chosen_model.name))
                    log(f"[3D] Paired (1:1): {Path(fps_paths[0]).name} -> {staged_models[0].name}")
                else:
                    # fuzzy map
                    # Build list of destination model paths (after copy)
                    dest_models = []
                    for m in staged_models:
                        sub = "step" if m.suffix.lower() in (".step",".stp") else "wrl"
                        dest_models.append((m, personal / "3d" / sub / m.name))
                    for mod_path in fps_paths:
                        try:
                            txt = Path(mod_path).read_text(encoding="utf-8", errors="ignore")
                            if re.search(r'\(model\s+', txt):  # already has model
                                continue
                            # match by module stem first
                            stem = Path(mod_path).stem
                            best = _choose_from_models([p for (_m,p) in dest_models], stem, prefer=prefer_model_ext)
                            if not best and sym_cands:
                                # try symbol names/values
                                for cand in sorted(sym_cands, key=len, reverse=True):
                                    best = _choose_from_models([p for (_m,p) in dest_models], cand, prefer=prefer_model_ext)
                                    if best: break
                            if best:
                                _write_model_into_footprint(mod_path, best, personal / fp_folder, force_relative_models)
                                res["models_paired"] += 1
                                res["models_paired_pairs"].append((Path(mod_path).name, best.name))
                                log(f"[3D] Paired: {Path(mod_path).name} -> {best.name}")
                        except Exception as e:
                            log(f"[3D] Pairing error for {Path(mod_path).name}: {e}")

        # Merge symbols last
        syms = find_files(staging, ["*.kicad_sym"])
        if syms:
            stats, merged = merge_symbols_strict(personal_sym, syms, fps_paths,
                                                 autoset_fp=autoset_fp, fp_nick=fp_nick, overwrite=overwrite,
                                                 mode=mode, ignore_footprint_for_dedupe=ignore_footprint_for_dedupe,
                                                 duplicate_mode=duplicate_mode, log=log)
            res["symbols_added"] = len(stats["added"])
            res["symbols_renamed"] = len(stats["renamed"])
            res["symbols_overwritten"] = len(stats["overwritten"])
            res["symbols_skipped_content_dupe"] = len(stats["skipped_content_dupe"])
            res["symbols_skipped_exact"] = len(stats.get("skipped_exact", []))
            res["symbol_names_debug"] = stats["debug_symbols"]
            res["symbols_added_names"] = list(stats["added"])
            res["symbols_renamed_pairs"] = list(stats["renamed"])
            res["symbols_overwritten_names"] = list(stats["overwritten"])
            if not dry_run:
                write_symbols_file(personal_sym, merged)
        if tmpdir: shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        res["errors"].append(str(e))
    return res

# --------------- Symbol-aware Fix 3D ---------------
def _footprint_to_symbol_cands(sym_path: Path):
    # Map: module_name -> set of textual candidates (symbol name, Value, MPN)
    out = {}
    if not sym_path.exists(): return out
    raw = sym_path.read_text(encoding="utf-8", errors="ignore")
    for name, block in parse_symbol_blocks(raw):
        m = re.search(r'\(\s*property\s+"Footprint"\s+"([^"]+)"', block)
        if not m: continue
        mod = m.group(1).split(":")[-1].strip()
        vals = {name}
        mv = re.search(r'\(\s*property\s+"Value"\s+"([^"]*)"', block);  vals.add(mv.group(1)) if mv and mv.group(1) else None
        mm = re.search(r'\(\s*property\s+"MPN"\s+"([^"]*)"', block);    vals.add(mm.group(1)) if mm and mm.group(1) else None
        out.setdefault(mod, set()).update(v for v in vals if v and len(v) >= 3)
    return out

def audit_footprints(fp_dir: Path, models_root: Path):
    rows = []
    exact, stems, canon, files = _available_models_index(models_root)
    for mod_path in sorted(fp_dir.glob("*.kicad_mod"), key=lambda p: p.name.lower()):
        txt = mod_path.read_text(encoding="utf-8", errors="ignore")
        models = [m.group(1) or m.group(2) for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]
        if not models:
            status, icon = ("None","❌"); found = 0; total = 0
        else:
            total = len(models); found = 0
            for mpath in models:
                cand = _choose_local_model_smart((exact, stems, canon, files), mpath)
                if cand: found += 1
            if found == 0: status, icon = ("None","❌")
            elif found == total: status, icon = ("All","✅")
            else: status, icon = ("Partial","⚠️")
        rows.append((mod_path.stem, status, icon, f"{found}/{total}"))
    return rows

def fix_footprint_models(fp_dir: Path, models_root: Path, sym_path: Path=None, dry_run: bool=True, add_if_missing: bool=True, prefer_ext: str="step", force_relative: bool=False):
    all_idx = _available_models_index(models_root)
    changed = 0; total = 0; modified = []
    sym_cands = _footprint_to_symbol_cands(sym_path) if sym_path else {}

    def relmaker(local_path: Path):
        try: rp = os.path.relpath(local_path, start=fp_dir)
        except Exception: rp = local_path.name
        return rp.replace("\\","/")

    for mod_path in sorted(fp_dir.glob("*.kicad_mod"), key=lambda p: p.name.lower()):
        total += 1
        txt = mod_path.read_text(encoding="utf-8", errors="ignore")
        models = [m.group(1) or m.group(2) for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]
        new_txt = txt
        def chooser(old): return _choose_local_model_smart(all_idx, old, prefer=prefer_ext)
        make_rel = (lambda p: relmaker(p)) if force_relative else None
        if models:
            new_txt = _rewrite_all_models(new_txt, chooser, make_rel=make_rel)
        elif add_if_missing:
            # try by module name first
            cand = _choose_local_model_smart(all_idx, mod_path.stem, prefer=prefer_ext)
            if not cand and sym_cands.get(mod_path.stem):
                # try symbol-aware candidates
                for key in sorted(sym_cands[mod_path.stem], key=len, reverse=True):
                    cand = _choose_local_model_smart(all_idx, key, prefer=prefer_ext)
                    if cand: break
            if cand:
                if force_relative:
                    rel = relmaker(cand)
                else:
                    sub = "step" if cand.suffix.lower() in (".step",".stp") else "wrl"
                    rel = f'${{PERSONAL_LIB_DIR}}/3d/{sub}/{cand.name}'
                insert = f'\n  (model "{rel}")\n'
                if new_txt.rstrip().endswith(')'):
                    new_txt = new_txt.rstrip()[:-1] + insert + ")\n"
                else:
                    new_txt = new_txt + insert
        # Collapse duplicate model entries before comparing
        new_txt = _dedupe_models_in_text(new_txt, fp_dir, Path(fp_dir).parent)
        if new_txt != txt:
            changed += 1; modified.append(mod_path)
            if not dry_run:
                bak = mod_path.with_suffix(".kicad_mod.bak")
                try: mod_path.rename(bak)
                except Exception: pass
                mod_path.write_text(new_txt, encoding="utf-8")

    return changed, total, modified

# -------------------- Delete Parts helpers --------------------
def list_symbols(sym_path: Path):
    if not sym_path.exists(): return []
    raw = sym_path.read_text(encoding="utf-8", errors="ignore")
    return parse_symbol_blocks(raw)

def get_symbol_footprint(block: str):
    m = re.search(r'\(\s*property\s+"Footprint"\s+"([^"]+)"', block)
    if not m: return None
    fp = m.group(1)
    return fp.split(":")[-1].strip() if ":" in fp else fp.strip()

def rebuild_symbols(sym_path: Path, keep: dict, backup: bool=True):
    header = '(kicad_symbol_lib (version 20211014) (generator "delete-tool-merged-4.2")\n'
    body = [header]
    for name in sorted(keep.keys(), key=lambda s: s.lower()):
        body.append(keep[name].rstrip() + "\n")
    body.append(")\n")
    if backup and sym_path.exists():
        bak = sym_path.with_suffix(".kicad_sym.bak")
        try: sym_path.rename(bak)
        except Exception:
            import shutil as _sh; _sh.copy2(sym_path, bak)
    sym_path.write_text("".join(body), encoding="utf-8")

def models_in_footprint(mod_path: Path):
    if not mod_path.exists(): return []
    txt = mod_path.read_text(encoding="utf-8", errors="ignore")
    return [m.group(1) or m.group(2) for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]

def expand_personal_var(path_str: str, personal_root: Path) -> Path:
    path_str = path_str.replace("\\", "/")
    if "${PERSONAL_LIB_DIR}" in path_str:
        rest = path_str.split("}", 1)[1].lstrip("/")
        return personal_root / rest
    return Path(path_str)

def _norm_model_key(path_str: str, fp_dir: Path, personal_root: Path) -> str:
    try:
        p = path_str.replace("\\", "/")
    except Exception:
        p = str(path_str)
    if "${PERSONAL_LIB_DIR}" in p:
        rest = p.split("}", 1)[1].lstrip("/")
        p = str((personal_root / rest).resolve())
    try:
        if not (p.startswith("/") or re.match(r'^[A-Za-z]:/', p)):
            p = str((fp_dir / p).resolve())
    except Exception:
        pass
    key = Path(p).name.lower()
    return key

def _extract_model_blocks(txt: str):
    """Extract complete (model ...) blocks using paren-counting, preserving transforms."""
    blocks = []
    pos = 0
    while True:
        match = re.search(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt[pos:])
        if not match:
            break
        model_start = pos + match.start()
        path = match.group(1) or match.group(2)
        # Count parens to find complete block
        paren_count = 0
        i = model_start
        while i < len(txt):
            if txt[i] == '(':
                paren_count += 1
            elif txt[i] == ')':
                paren_count -= 1
                if paren_count == 0:
                    i += 1
                    break
            i += 1
        full_block = txt[model_start:i]
        blocks.append((model_start, i, path, full_block))
        pos = i
    return blocks

def _dedupe_models_in_text(txt: str, fp_dir: Path, personal_root: Path) -> str:
    blocks = _extract_model_blocks(txt)
    if not blocks:
        return txt
    seen = set()
    to_remove = []
    for start, end, path, full_block in blocks:
        key = _norm_model_key(path, fp_dir, personal_root)
        if key in seen:
            to_remove.append((start, end))
        else:
            seen.add(key)
    if not to_remove:
        return txt
    # Remove duplicate blocks back-to-front to preserve indices
    result = txt
    for start, end in reversed(to_remove):
        # Also remove leading whitespace on the same line
        ws_start = start
        while ws_start > 0 and result[ws_start - 1] in (' ', '\t'):
            ws_start -= 1
        # Remove trailing newline if present
        ws_end = end
        if ws_end < len(result) and result[ws_end] == '\n':
            ws_end += 1
        result = result[:ws_start] + result[ws_end:]
    return result



# -------------------- PATH UTILITIES (Tkinter) --------------------
def _windows_kicad_config_dirs():
    paths = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = Path(appdata) / "kicad"
        if root.exists():
            for p in root.iterdir():
                if p.is_dir():
                    paths.append(p)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        lroot = Path(local) / "kicad"
        if lroot.exists():
            for p in lroot.iterdir():
                if p.is_dir() and p not in paths:
                    paths.append(p)
    return sorted(paths, key=lambda p: p.name, reverse=True)

def _all_kicad_config_dirs():
    """Cross-platform KiCad config dirs (and their version subdirs).

    Unlike _windows_kicad_config_dirs(), this also covers macOS and Linux so the
    nickname auto-detector works for anyone the tool is shared with."""
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "kicad")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "kicad")
    roots.append(Path.home() / "Library" / "Preferences" / "kicad")  # macOS
    roots.append(Path.home() / ".config" / "kicad")                  # Linux/XDG
    out, seen = [], set()
    for root in roots:
        try:
            if not root.exists():
                continue
            subs = sorted([p for p in root.iterdir() if p.is_dir()],
                          key=lambda p: p.name, reverse=True)
            for cand in [root] + subs:
                rp = cand.resolve()
                if rp not in seen:
                    seen.add(rp)
                    out.append(cand)
        except Exception:
            pass
    return out


def _kicad_path_vars(personal_root: Path):
    """Collect KiCad path-substitution variables from kicad_common.json files so
    that ${VAR}-style URIs in fp-lib-table can be expanded."""
    vars_map = {}
    for d in _all_kicad_config_dirs():
        cfg = d / "kicad_common.json"
        try:
            if cfg.exists():
                data = json.loads(cfg.read_text(encoding="utf-8", errors="ignore"))
                for k, v in (data.get("path", {}) or {}).items():
                    if isinstance(v, str) and k not in vars_map:
                        vars_map[k] = v
        except Exception:
            pass
    # If the user hasn't defined PERSONAL_LIB_DIR, assume it points at this library.
    vars_map.setdefault("PERSONAL_LIB_DIR", str(personal_root))
    return vars_map


def _expand_kicad_uri(uri: str, vars_map: dict) -> str:
    """Expand a fp-lib-table URI into a filesystem path: handle file://,
    ${VAR}/$VAR substitution (KiCad vars first, then OS env), and ~."""
    s = uri.replace("\\", "/").strip()
    if s.lower().startswith("file://"):
        s = s[7:]

    def _sub(m):
        name = m.group(1) or m.group(2)
        if name in vars_map:
            return vars_map[name]
        return os.environ.get(name, m.group(0))

    s = re.sub(r'\$\{([A-Za-z0-9_]+)\}|\$([A-Za-z0-9_]+)', _sub, s)
    return os.path.expanduser(s)


def _detect_fp_nickname(personal_root: Path, pretty_name: str = "footprints.pretty"):
    """Determine the footprint-library nickname KiCad uses for this library's
    .pretty directory.

    KiCad references footprints as <nickname>:<Footprint>, where <nickname> is the
    name registered for the .pretty folder in fp-lib-table -- NOT the .kicad_sym
    filename, and NOT a value this tool can invent. So we read the user's
    fp-lib-table(s) and find the entry whose URI resolves to this .pretty dir.

    Falls back to the .pretty folder stem (KiCad's GUI default when you Add a
    library) if no registered entry is found. Returns None only when no library
    path is set."""
    if not personal_root:
        return None
    try:
        target = (Path(personal_root) / pretty_name).resolve()
    except Exception:
        return None

    vars_map = _kicad_path_vars(Path(personal_root))
    target_norm = os.path.normcase(os.path.normpath(str(target)))

    # fp-lib-table candidates: global config dirs, the library dir itself, and its
    # parent (covers project-local tables).
    tables = [d / "fp-lib-table" for d in _all_kicad_config_dirs()]
    tables.append(Path(personal_root) / "fp-lib-table")
    tables.append(Path(personal_root).parent / "fp-lib-table")

    for tbl in tables:
        try:
            if not tbl.exists():
                continue
            txt = tbl.read_text(encoding="utf-8", errors="ignore")
            # KiCad writes exactly one (name ...) then one (uri ...) per (lib ...),
            # so positional pairing is reliable.
            names = re.findall(r'\(name\s+"([^"]*)"\)', txt)
            uris = re.findall(r'\(uri\s+"([^"]*)"\)', txt)
            for name, uri in zip(names, uris):
                cand = _expand_kicad_uri(uri, vars_map)
                try:
                    cp = Path(cand)
                    if cp.exists() and cp.resolve().samefile(target):
                        return name
                except Exception:
                    pass
                # String comparison fallback: a shared library's paths may not
                # exist on the machine reading the table, but still match by value.
                if os.path.normcase(os.path.normpath(cand)) == target_norm:
                    return name
        except Exception:
            pass

    # Fallback: KiCad pre-fills the nickname from the .pretty folder stem.
    stem = pretty_name
    if stem.lower().endswith(".pretty"):
        stem = stem[:-len(".pretty")]
    return stem or None


def _update_kicad_path_variable(personal_root: Path, var_name="PERSONAL_LIB_DIR", log=lambda *a: None):
    dirs = _windows_kicad_config_dirs()
    updated = []
    for d in dirs:
        cfg = d / "kicad_common.json"
        try:
            if not cfg.exists():
                continue
            data = json.loads(cfg.read_text(encoding="utf-8"))
            paths = data.get("path", {})
            cur = paths.get(var_name)
            new = str(personal_root)
            if cur != new:
                paths[var_name] = new
                data["path"] = paths
                cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
                updated.append(cfg)
                log(f"[Paths] Set {var_name} = {new} in {cfg}")
        except Exception as e:
            log(f"[Paths][WARN] Failed to update {cfg}: {e}")
    return updated

def _rewrite_fp_lib_table(personal_root: Path, var_name="PERSONAL_LIB_DIR", log=lambda *a: None):
    dirs = _windows_kicad_config_dirs()
    changed = []
    target_abs = (personal_root / "footprints.pretty").resolve()
    target_var = f"${{{var_name}}}/footprints.pretty"
    pat = re.compile(r'\(uri\s+"([^"]+)"\)')
    for d in dirs:
        tbl = d / "fp-lib-table"
        if not tbl.exists():
            continue
        txt = tbl.read_text(encoding="utf-8", errors="ignore")
        def repl(m):
            uri = m.group(1)
            u_norm = uri.replace("\\", "/")
            try:
                u_path = u_norm
                if u_path.lower().startswith("file://"):
                    u_path = u_path[7:]
                if Path(u_path).exists():
                    if Path(u_path).resolve().samefile(target_abs):
                        return f'(uri "{target_var}")'
            except Exception:
                pass
            try:
                abs_path = Path(u_norm)
                if abs_path.exists():
                    root = personal_root.resolve()
                    ap = abs_path.resolve()
                    if str(ap).lower().startswith(str(root).lower()):
                        rel = ap.relative_to(root)
                        path_rel = str(rel).replace("\\", "/")
                        return f'(uri "${{{var_name}}}/{path_rel}")'
            except Exception:
                pass
            return m.group(0)
        new = pat.sub(repl, txt)
        if new != txt:
            tbl.write_text(new, encoding="utf-8")
            changed.append(tbl)
            log(f"[Paths] Rewrote URIs in {tbl}")
    return changed

def _rewrite_models_in_pretty(pretty_dir: Path, personal_root: Path, var_name="PERSONAL_LIB_DIR", log=lambda *a: None):
    cnt_changed = 0
    for mod in Path(pretty_dir).glob("*.kicad_mod"):
        try:
            txt = mod.read_text(encoding="utf-8", errors="ignore")
            def repl(m):
                inner = m.group(1) or m.group(2)
                s = inner.replace("\\","/")
                try:
                    p = Path(s)
                    if not (s.startswith("${") or s.lower().startswith("file://")):
                        rp = p
                        if not (p.is_absolute() and p.exists()):
                            rp = (Path(pretty_dir) / p).resolve()
                        root = Path(personal_root).resolve()
                        if str(rp).lower().startswith(str(root).lower()):
                            rel = rp.relative_to(root)
                            path_rel = str(rel).replace("\\", "/")
                            return f'(model "${{{var_name}}}/{path_rel}")'
                except Exception:
                    pass
                return f'(model "{inner}")'
            new_txt = re.sub(r'\(model\s+(?:"([^"]+)"|([^\s)]+))\s*\)', repl, txt)
            try:
                new_txt = _dedupe_models_in_text(new_txt, Path(pretty_dir), Path(personal_root))
            except Exception:
                pass
            if new_txt != txt:
                mod.write_text(new_txt, encoding="utf-8")
                cnt_changed += 1
        except Exception as e:
            log(f"[Paths][WARN] Failed {mod}: {e}")
    try:
        pdir = Path(pretty_dir)
        if pdir.exists():
            os.utime(str(pdir), None)
    except Exception:
        pass
    return cnt_changed

def _rewrite_models(personal_root: Path, var_name="PERSONAL_LIB_DIR", log=lambda *a: None):
    """Rewrite 3D model paths in the footprints.pretty directory under personal_root."""
    pretty_dir = personal_root / "footprints.pretty"
    if not pretty_dir.exists():
        log(f"[Paths] Footprint directory not found: {pretty_dir}")
        return 0
    return _rewrite_models_in_pretty(pretty_dir, personal_root, var_name=var_name, log=log)


# ═══════════════════════════════════════════════════════════════════════════
# 3D MODEL VALIDATION & RESOLUTION (used by the 3D Models tab)
# ═══════════════════════════════════════════════════════════════════════════

def resolve_model_path(path_str: str, fp_dir: Path, personal_root: Path) -> tuple:
    """Resolve a (model "...") path string to a real filesystem Path.

    Handles ${PERSONAL_LIB_DIR} substitution, absolute paths, relative paths
    (resolved against the footprint directory), and Windows backslash
    separators that may have leaked in.

    Returns (Path, bool exists). The returned Path is always an attempt at
    resolution even if the file does not exist.
    """
    if not path_str:
        return Path(""), False
    p = path_str.replace("\\", "/")
    try:
        if "${PERSONAL_LIB_DIR}" in p:
            rest = p.split("}", 1)[1].lstrip("/")
            resolved = (personal_root / rest)
        elif p.startswith("/") or re.match(r'^[A-Za-z]:/', p):
            resolved = Path(p)
        else:
            resolved = (fp_dir / p)
        try:
            resolved = resolved.resolve()
        except Exception:
            pass
        return resolved, resolved.exists()
    except Exception:
        return Path(p), False


def fix_corrupted_model_paths(fp_path: Path) -> int:
    """In-place: convert backslashes inside (model "...") path strings to
    forward slashes. Preserves all transform data (offset, scale, rotate).
    Returns the number of paths that were rewritten.
    """
    try:
        txt = fp_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    blocks = _extract_model_blocks(txt)
    if not blocks:
        return 0

    fixed = 0
    pieces = []
    last_end = 0
    for start, end, path, full_block in blocks:
        if "\\" in path:
            new_path = path.replace("\\", "/")
            new_block = re.sub(
                r'(\(model\s+)(?:"[^"]+"|[^\s)]+)',
                lambda m, np=new_path: f'{m.group(1)}"{np}"',
                full_block,
                count=1,
            )
            pieces.append(txt[last_end:start])
            pieces.append(new_block)
            last_end = end
            fixed += 1
    if fixed == 0:
        return 0
    pieces.append(txt[last_end:])
    new_txt = "".join(pieces)
    try:
        fp_path.write_text(new_txt, encoding="utf-8")
        return fixed
    except Exception:
        return 0


def find_model_for_footprint(fp_name: str, models_index, prefer_ext: str = "step"):
    """Search the models index for a 3D model matching a footprint name.

    Search order:
      1. Exact stem match (case-insensitive)
      2. Canonicalized stem match (ignores punctuation/case)
      3. Fuzzy match (n-gram score), threshold 0.62

    Returns (Path or None, confidence float 0..1, method str).
    method ∈ {"exact", "canon", "fuzzy", "none"}.
    """
    if not models_index:
        return None, 0.0, "none"
    exact, stems, canon, files = models_index
    if not files:
        return None, 0.0, "none"

    fp_lower = fp_name.lower()
    if fp_lower in stems:
        return _prefer_ext(stems[fp_lower], prefer_ext), 1.0, "exact"

    fp_canon = _canon(fp_name)
    if fp_canon in canon:
        return _prefer_ext(canon[fp_canon], prefer_ext), 0.95, "canon"

    best, best_score = None, 0.0
    for f in files:
        s = _score(_canon(f.stem), fp_canon)
        if s > best_score:
            best, best_score = f, s
    if best and best_score >= 0.62:
        return best, best_score, "fuzzy"
    return None, best_score, "none"


def analyze_footprint_3d(fp_path: Path, models_root: Path, personal_root: Path,
                         models_index=None, prefer_ext: str = "step") -> dict:
    """Inspect a single footprint for 3D model status.

    This function does NOT modify the footprint file. Backslash-corrupted
    paths are flagged via ``info["corrupted"]`` so the caller can present
    them for confirmation; use ``fix_corrupted_model_paths`` to repair them
    once the user has approved.

    Returns a dict with keys:
        fp_path, fp_name, status, raw_path, resolved, file_exists, corrupted,
        suggested, suggested_confidence, suggested_method, error

    status ∈ {"ok", "missing", "no_path", "error"}
    Note: "found_*" is no longer returned -- whether a suggestion exists is
    indicated by ``suggested is not None``. The UI buckets rows itself.
    """
    info = {
        "fp_path": fp_path,
        "fp_name": fp_path.stem,
        "status": "unknown",
        "raw_path": "",
        "resolved": None,
        "file_exists": False,
        "corrupted": False,
        "suggested": None,
        "suggested_confidence": 0.0,
        "suggested_method": "",
        "error": "",
    }

    try:
        txt = fp_path.read_text(encoding="utf-8", errors="ignore")
        matches = re.findall(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)
        paths = [a or b for (a, b) in matches]

        # Flag legacy-corrupted paths (Windows backslash separators inside
        # the (model "...") string). The path resolver below already
        # tolerates them, but the file-on-disk still needs to be cleaned
        # before KiCad will read it correctly.
        info["corrupted"] = any("\\" in p for p in paths)

        if not paths:
            info["status"] = "no_path"
            if models_index is None:
                models_index = _available_models_index(models_root)
            sug, conf, method = find_model_for_footprint(fp_path.stem, models_index, prefer_ext)
            if sug is not None:
                info["suggested"] = sug
                info["suggested_confidence"] = conf
                info["suggested_method"] = method
            return info

        first = paths[0]
        info["raw_path"] = first
        resolved, exists = resolve_model_path(first, fp_path.parent, personal_root)
        info["resolved"] = resolved
        info["file_exists"] = exists
        if exists:
            info["status"] = "ok"
        else:
            info["status"] = "missing"
            if models_index is None:
                models_index = _available_models_index(models_root)
            sug, conf, method = find_model_for_footprint(fp_path.stem, models_index, prefer_ext)
            if sug is not None:
                info["suggested"] = sug
                info["suggested_confidence"] = conf
                info["suggested_method"] = method
        return info
    except Exception as e:
        info["status"] = "error"
        info["error"] = str(e)
        return info


def replace_model_path_in_footprint(fp_path: Path, new_model_path: Path,
                                    fp_dir: Path, personal_root: Path,
                                    use_relative: bool = True,
                                    keep_existing_transforms: bool = True) -> bool:
    """Replace (or add, if absent) the FIRST (model ...) entry in a footprint
    with a reference to ``new_model_path``. Preserves transform data of the
    existing entry when keep_existing_transforms is True.

    use_relative=True writes a path relative to fp_dir.
    use_relative=False writes ``${PERSONAL_LIB_DIR}/3d/<sub>/<name>``.

    Returns True on a write, False on no-op.
    """
    try:
        txt = fp_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    if use_relative:
        try:
            rel = os.path.relpath(new_model_path, start=fp_dir).replace("\\", "/")
        except Exception:
            rel = new_model_path.name
        new_path_str = rel
    else:
        sub = "step" if new_model_path.suffix.lower() in (".step", ".stp") else "wrl"
        new_path_str = f'${{PERSONAL_LIB_DIR}}/3d/{sub}/{new_model_path.name}'

    blocks = _extract_model_blocks(txt)
    if blocks:
        # Replace path of the FIRST model block, preserve everything else
        start, end, _path, full_block = blocks[0]
        new_block = re.sub(
            r'(\(model\s+)(?:"[^"]+"|[^\s)]+)',
            lambda m, np=new_path_str: f'{m.group(1)}"{np}"',
            full_block,
            count=1,
        )
        new_txt = txt[:start] + new_block + txt[end:]
    else:
        # No existing model — append a fresh one inside the closing paren
        insert = f'\n  (model "{new_path_str}")\n'
        if txt.rstrip().endswith(")"):
            new_txt = txt.rstrip()[:-1] + insert + ")\n"
        else:
            new_txt = txt + insert

    if new_txt == txt:
        return False
    try:
        fp_path.write_text(new_txt, encoding="utf-8")
        return True
    except Exception:
        return False


# -------------------- GUI --------------------


# ═══════════════════════════════════════════════════════════════════════════
# MODERN THEME SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

# GitHub Dark inspired color scheme
COLORS = {
    'bg': '#0d1117', 'panel': '#161b22', 'border': '#30363d',
    'text': '#c9d1d9', 'text_muted': '#8b949e',
    'accent': '#1f6feb', 'success': '#3fb950',
    'warning': '#d29922', 'error': '#f85149',
    'hover': '#21262d',
}

def apply_modern_theme(root):
    """Apply modern dark theme to tkinter app."""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    
    # Base styles
    style.configure(".", background=COLORS['bg'], foreground=COLORS['text'], 
                   fieldbackground=COLORS['panel'], bordercolor=COLORS['border'])
    style.configure("TFrame", background=COLORS['bg'])
    style.configure("TLabel", background=COLORS['bg'], foreground=COLORS['text'], font=("Segoe UI", 10))
    style.configure("TButton", background=COLORS['panel'], foreground=COLORS['text'], 
                   bordercolor=COLORS['border'], font=("Segoe UI", 10, "bold"), padding=8)
    style.map("TButton", background=[("active", COLORS['hover'])])
    
    # Accent button style
    style.configure("Accent.TButton", background=COLORS['accent'], foreground='white')
    style.map("Accent.TButton", background=[("active", "#1a56db")])
    
    # Entry and combobox
    style.configure("TEntry", fieldbackground=COLORS['panel'], foreground=COLORS['text'], 
                   insertcolor=COLORS['text'], font=("Segoe UI", 10))
    style.configure("TCombobox", fieldbackground=COLORS['panel'], foreground=COLORS['text'], 
                   arrowcolor=COLORS['text'], font=("Segoe UI", 10))
    # clam paints readonly comboboxes light grey, which hides the light text
    style.map("TCombobox",
             fieldbackground=[("readonly", COLORS['panel'])],
             foreground=[("readonly", COLORS['text'])],
             selectbackground=[("readonly", COLORS['panel'])],
             selectforeground=[("readonly", COLORS['text'])])
    root.option_add("*TCombobox*Listbox.background", COLORS['panel'])
    root.option_add("*TCombobox*Listbox.foreground", COLORS['text'])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS['accent'])

    # Checkbuttons
    style.configure("TCheckbutton", background=COLORS['bg'], foreground=COLORS['text'], 
                   font=("Segoe UI", 10))
    
    # Labelframe
    style.configure("TLabelframe", background=COLORS['panel'], bordercolor=COLORS['border'], 
                   relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=COLORS['panel'], foreground=COLORS['text'], 
                   font=("Segoe UI", 10, "bold"))
    
    # Modern flat tabs (NOT recessed!)
    style.configure("TNotebook", background=COLORS['bg'], borderwidth=0, tabmargins=[2, 5, 2, 0])
    style.configure("TNotebook.Tab", background=COLORS['bg'], foreground=COLORS['text_muted'],
                   padding=[16, 8], font=("Segoe UI", 10, "bold"), borderwidth=0)
    
    # Active tab: BIGGER with more padding, lighter background + accent bottom border
    style.map("TNotebook.Tab",
             background=[("selected", COLORS['panel'])],
             foreground=[("selected", COLORS['text'])],
             padding=[("selected", [24, 12])],  # Bigger active tab!
             font=[("selected", ("Segoe UI", 11, "bold"))],  # Slightly larger font
             expand=[("selected", [1, 1, 1, 3])])  # Accent border on bottom
    
    # Treeview
    style.configure("Treeview", background=COLORS['panel'], fieldbackground=COLORS['panel'],
                   foreground=COLORS['text'], bordercolor=COLORS['border'], rowheight=28,
                   font=("Segoe UI", 10))
    style.configure("Treeview.Heading", background=COLORS['bg'], foreground=COLORS['text'],
                   font=("Segoe UI", 10, "bold"), borderwidth=1, relief="flat")
    style.map("Treeview", background=[("selected", COLORS['accent'])])
    
    # Configure root window
    root.configure(bg=COLORS['bg'])
    
    return style


# ═══════════════════════════════════════════════════════════════════════════  
# MAIN APPLICATION CLASS
# ═══════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    """KiCad PartBench main window."""
    
    def __init__(self):
        super().__init__()
        
        self.title(f"⚡ {APP_NAME} {APP_VERSION}")
        self.geometry("1400x900")
        self.minsize(1200, 700)
        
        # Apply modern theme
        self.style = apply_modern_theme(self)
        
        # State variables
        self.source = tk.StringVar()
        self.personal = tk.StringVar()
        self.nick = tk.StringVar(value="Personal_Footprints")
        # When on, the footprint nickname is auto-detected from the user's
        # fp-lib-table whenever the personal library is selected. Off = the
        # value typed in the Footprint Nickname field is used verbatim.
        self.auto_nick = tk.BooleanVar(value=True)
        self.autoset = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.dup_handling = tk.StringVar(value="skip")  # skip, replace, or rename
        self.recurse = tk.BooleanVar(value=True)
        self.ignore_fp_dedupe = tk.BooleanVar(value=True)
        self.dedupe_mode = tk.StringVar(value="Ignore Properties")
        self.dry_run = tk.BooleanVar(value=False)
        # Skip packages already in the import log -- speeds up incremental
        # imports massively. The user can clear the log from Tools menu.
        self.skip_imported = tk.BooleanVar(value=True)
        self.prefer_pkg = tk.StringVar(value="folder")
        self.sym_choice = tk.StringVar(value="symbols.kicad_sym")
        self.fp_choice = tk.StringVar(value="footprints.pretty")
        self.models_root = tk.StringVar()
        self.rel_models = tk.BooleanVar(value=True)
        self.force_relative_models = self.rel_models
        
        # Recent directories
        self.recent_sources = []
        self.recent_personal = []
        
        # Results storage
        self.results = []
        
        # Load preferences
        prefs = self.load_prefs()
        self.source.set(prefs.get("last_source_dir", ""))
        self.personal.set(prefs.get("last_personal_dir", ""))
        self.sym_choice.set(prefs.get("sym_choice", "symbols.kicad_sym"))
        self.fp_choice.set(prefs.get("fp_choice", "footprints.pretty"))
        self.models_root.set(prefs.get("models_root", ""))
        self.rel_models.set(prefs.get("rel_models", True))
        self.nick.set(prefs.get("footprint_nickname", "Personal_Footprints"))
        self.auto_nick.set(prefs.get("auto_detect_nickname", True))
        # Re-detect the nickname if the .pretty folder choice changes, and once on
        # startup for the restored library (deferred so widgets exist first).
        try:
            self.fp_choice.trace_add("write", lambda *a: self.detect_and_set_nickname())
            self.after(300, self.detect_and_set_nickname)
        except Exception:
            pass
        self.autoset.set(prefs.get("autoset", True))
        self.skip_imported.set(prefs.get("skip_imported", True))
        # Inbox tab: browser download folder + filter
        self.downloads_dir = tk.StringVar(
            value=prefs.get("downloads_dir", str(Path.home() / "Downloads")))
        self.inbox_zips_only = tk.BooleanVar(value=prefs.get("inbox_zips_only", True))
        self.recent_sources = prefs.get("recent_sources", [])[:10]
        self.recent_personal = prefs.get("recent_personal", [])[:10]
        
        # Create GUI
        self._create_menu()
        self._create_main_layout()
        self._create_status_bar()
        
        # Keyboard shortcuts
        self.bind("<Control-o>", lambda e: self.pick_source())
        self.bind("<Control-l>", lambda e: self.pick_personal())
        self.bind("<Control-r>", lambda e: self.run())
        self.bind("<Control-e>", lambda e: self.export_report())
        self.bind("<Control-q>", lambda e: self.quit())
        
    def _create_menu(self):
        """Create menu bar."""
        menubar = tk.Menu(self, bg=COLORS['panel'], fg=COLORS['text'], 
                         activebackground=COLORS['hover'], activeforeground=COLORS['text'])
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0, bg=COLORS['panel'], fg=COLORS['text'],
                           activebackground=COLORS['accent'], activeforeground='white')
        file_menu.add_command(label="Open Source Directory...", command=self.pick_source, accelerator="Ctrl+O")
        file_menu.add_command(label="Open Personal Library...", command=self.pick_personal, accelerator="Ctrl+L")
        file_menu.add_separator()
        file_menu.add_command(label="Export Report...", command=self.export_report, accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit, accelerator="Ctrl+Q")
        menubar.add_cascade(label="File", menu=file_menu)
        
        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0, bg=COLORS['panel'], fg=COLORS['text'],
                            activebackground=COLORS['accent'], activeforeground='white')
        tools_menu.add_command(label="View Library Parts", command=self.view_library_parts)
        tools_menu.add_command(label="Audit Footprints (3D)", command=self.audit_footprints_gui)
        tools_menu.add_command(label="Fix 3D Paths...", command=self.fix_3d_paths_gui)
        tools_menu.add_command(label="Delete Parts...", command=self.delete_parts_gui)
        tools_menu.add_separator()
        tools_menu.add_command(label="View / Reset Import Log...",
                               command=self.show_import_log_gui)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0, bg=COLORS['panel'], fg=COLORS['text'],
                           activebackground=COLORS['accent'], activeforeground='white')
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        
        self.config(menu=menubar)
    
    def _create_main_layout(self):
        """Create main tabbed layout."""
        # Create notebook for tabs
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Create tabs
        tab_inbox = ttk.Frame(notebook)
        tab_import = ttk.Frame(notebook)
        tab_library = ttk.Frame(notebook)
        tab_3d = ttk.Frame(notebook)
        tab_bom = ttk.Frame(notebook)
        tab_multipart = ttk.Frame(notebook)
        tab_passives = ttk.Frame(notebook)
        tab_settings = ttk.Frame(notebook)
        tab_results = ttk.Frame(notebook)
        
        notebook.add(tab_inbox, text=" 📥 Inbox ")
        notebook.add(tab_import, text=" 📁 Import ")
        notebook.add(tab_library, text=" 📚 Library ")
        notebook.add(tab_3d, text=" 🎲 3D Models ")
        notebook.add(tab_bom, text=" 📋 BOM Manager ")
        notebook.add(tab_multipart, text=" 🔌 Multi-part CPL ")
        notebook.add(tab_passives, text=" 🧩 Passives ")
        notebook.add(tab_settings, text=" ⚙️ Settings ")
        notebook.add(tab_results, text=" 📊 Results ")
        
        self._create_inbox_tab(tab_inbox)
        self._create_import_tab(tab_import)
        self._create_library_tab(tab_library)
        self._create_3d_models_tab(tab_3d)
        self._create_bom_manager_tab(tab_bom)
        self._create_multipart_tab(tab_multipart)
        self._create_passives_tab(tab_passives)
        self._create_settings_tab(tab_settings)
        self._create_results_tab(tab_results)
        
        self.notebook = notebook
        
        # Auto-refresh library when tab is selected
        # Match tabs by label, not index, so inserting a tab can't break this.
        def on_tab_change(event):
            label = (notebook.tab(notebook.select(), "text") or "").lower()
            if "inbox" in label:
                self._inbox_refresh(force=True)
            elif "library" in label:
                self.refresh_library_browser()
            elif "3d models" in label:
                # Only auto-scan the first time the user lands on the tab
                if not getattr(self, "_3d_tab_first_visit_done", False):
                    self._3d_tab_first_visit_done = True
                    self.after(50, self.scan_3d_models_tab)
        
        notebook.bind("<<NotebookTabChanged>>", on_tab_change)
        # Start the Inbox auto-refresh loop (only does work while it's visible)
        self.after(500, self._inbox_poll)

    
    def _create_import_tab(self, parent):
        """Create the import tab with modern layout."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)
        
        r = 0
        
        # Source & Destination section
        lf = ttk.Labelframe(parent, text="Source & Destination", padding=16)
        lf.grid(row=r, column=0, sticky="ew", padx=16, pady=(16,8)); r += 1
        lf.grid_columnconfigure(1, weight=1)
        
        ttk.Label(lf, text="Source Package(s):").grid(row=0, column=0, sticky="e", padx=(0,8), pady=6)
        ttk.Entry(lf, textvariable=self.source).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        btn_frame = ttk.Frame(lf)
        btn_frame.grid(row=0, column=2, padx=4, pady=6)
        ttk.Button(btn_frame, text="📁 Browse", command=self.pick_source).pack(side="left", padx=2)
        
        ttk.Label(lf, text="Personal Library:").grid(row=1, column=0, sticky="e", padx=(0,8), pady=6)
        ttk.Entry(lf, textvariable=self.personal).grid(row=1, column=1, sticky="ew", padx=4, pady=6)
        btn_frame2 = ttk.Frame(lf)
        btn_frame2.grid(row=1, column=2, padx=4, pady=6)
        ttk.Button(btn_frame2, text="📁 Browse", command=self.pick_personal).pack(side="left", padx=2)
        
        # Library Configuration section
        lf2 = ttk.Labelframe(parent, text="Library Configuration", padding=16)
        lf2.grid(row=r, column=0, sticky="ew", padx=16, pady=8); r += 1
        lf2.grid_columnconfigure((1,3), weight=1)
        
        ttk.Label(lf2, text="Symbol File:").grid(row=0, column=0, sticky="e", padx=(0,8), pady=6)
        self.sym_combo = ttk.Combobox(lf2, textvariable=self.sym_choice, values=["symbols.kicad_sym"])
        self.sym_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        
        ttk.Label(lf2, text="Footprint Folder:").grid(row=0, column=2, sticky="e", padx=(16,8), pady=6)
        self.fp_combo = ttk.Combobox(lf2, textvariable=self.fp_choice, values=["footprints.pretty"])
        self.fp_combo.grid(row=0, column=3, sticky="ew", padx=4, pady=6)
        
        ttk.Label(lf2, text="3D Models Root:").grid(row=1, column=0, sticky="e", padx=(0,8), pady=6)
        ttk.Entry(lf2, textvariable=self.models_root).grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=6)
        ttk.Button(lf2, text="📁", command=self.pick_models_root).grid(row=1, column=3, sticky="e", padx=4, pady=6)
        
        # Import Options section
        lf3 = ttk.Labelframe(parent, text="Import Options", padding=16)
        lf3.grid(row=r, column=0, sticky="ew", padx=16, pady=8); r += 1
        lf3.grid_columnconfigure((0,1), weight=1)
        
        ttk.Checkbutton(lf3, text="Auto-set footprint properties", variable=self.autoset).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(lf3, text="Use relative model paths", variable=self.rel_models).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        
        ttk.Checkbutton(lf3, text="Overwrite existing components", variable=self.overwrite).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(lf3, text="Recursive directory search", variable=self.recurse).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        
        # Duplicate handling dropdown
        dup_frame = ttk.Frame(lf3)
        dup_frame.grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(dup_frame, text="If symbol exists:").pack(side="left", padx=(0,8))
        dup_combo = ttk.Combobox(dup_frame, textvariable=self.dup_handling, 
                                 values=["skip", "replace", "rename"], state="readonly", width=12)
        dup_combo.pack(side="left")
        
        ttk.Checkbutton(lf3, text="Ignore footprint in deduplication", variable=self.ignore_fp_dedupe).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        
        ttk.Checkbutton(lf3, text="Dry run (preview only)", variable=self.dry_run).grid(row=3, column=0, sticky="w", padx=4, pady=4)
        
        # Skip-already-imported (the speed-up). Tip label sits next to it.
        skip_frame = ttk.Frame(lf3)
        skip_frame.grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 4))
        ttk.Checkbutton(
            skip_frame,
            text="Skip already-imported packages (faster on incremental imports)",
            variable=self.skip_imported,
        ).pack(side="left")
        ttk.Button(skip_frame, text="📜 View / reset import log…",
                   command=self.show_import_log_gui).pack(side="left", padx=(16, 0))
        
        # Action buttons
        btn_container = ttk.Frame(parent)
        btn_container.grid(row=r, column=0, sticky="ew", padx=16, pady=16); r += 1
        
        ttk.Button(btn_container, text="  ▶️ Run Import  ", command=self.run, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(btn_container, text="  📄 Export Report  ", command=self.export_report).pack(side="left", padx=4)
        ttk.Button(btn_container, text="  🧹 Clear Results  ", command=self.clear_results).pack(side="left", padx=4)
    
    def _create_library_tab(self, parent):
        """Create library management tab with built-in browser."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)
        
        # Search/Filter section
        search_frame = ttk.Frame(parent)
        search_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16,8))
        search_frame.grid_columnconfigure(1, weight=1)
        
        ttk.Label(search_frame, text="🔍 Search:").grid(row=0, column=0, padx=(0,8), pady=4)
        self.lib_search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.lib_search_var, font=("Segoe UI", 10))
        search_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        
        btn_frame = ttk.Frame(search_frame)
        btn_frame.grid(row=0, column=2, padx=4)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_library_browser).pack(side="left", padx=2)
        self.lib_count_label = ttk.Label(btn_frame, text="", foreground=COLORS['text_muted'])
        self.lib_count_label.pack(side="left", padx=8)
        
        # Info bar
        info_frame = ttk.Frame(parent)
        info_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0,8))
        self.lib_info_var = tk.StringVar(value="Select a library to view parts...")
        ttk.Label(info_frame, textvariable=self.lib_info_var, foreground=COLORS['text_muted']).pack(side="left")
        
        # Library browser tree
        tree_frame = ttk.Frame(parent)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0,8))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        
        cols = ("symbol", "footprint", "mpn", "model_3d", "pins")
        self.lib_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=20)
        self.lib_tree.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.lib_tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.lib_tree.configure(yscrollcommand=vsb.set)
        
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.lib_tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.lib_tree.configure(xscrollcommand=hsb.set)
        
        # Configure columns
        self.lib_tree.heading("symbol", text="Symbol Name", command=lambda: self.sort_lib_tree("symbol"))
        self.lib_tree.heading("footprint", text="Footprint", command=lambda: self.sort_lib_tree("footprint"))
        self.lib_tree.heading("mpn", text="MPN", command=lambda: self.sort_lib_tree("mpn"))
        self.lib_tree.heading("model_3d", text="3D Model", command=lambda: self.sort_lib_tree("model_3d"))
        self.lib_tree.heading("pins", text="Pins", command=lambda: self.sort_lib_tree("pins"))
        
        self.lib_tree.column("symbol", width=200, anchor="w")
        self.lib_tree.column("footprint", width=220, anchor="w")
        self.lib_tree.column("mpn", width=180, anchor="w")
        self.lib_tree.column("model_3d", width=150, anchor="w")
        self.lib_tree.column("pins", width=60, anchor="center")
        
        # Tool buttons at bottom
        tool_frame = ttk.Labelframe(parent, text="Library Tools", padding=12)
        tool_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(8,16))
        
        ttk.Button(tool_frame, text="⚙️ Standardize MPN Fields", command=self.standardize_mpn_gui, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🗑️ Clean Duplicates", command=self.clean_duplicates_gui, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(tool_frame, text="✅ Validate Files", command=self.validate_files_gui, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🎨 Assign 3D Model", command=self.manual_3d_assign_gui, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🔧 Repair/Restore Library", command=self.repair_library_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🔍 Audit Footprints", command=self.audit_footprints_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🔧 Fix 3D Paths", command=self.fix_3d_paths_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🗑️ Delete Parts", command=self.delete_parts_gui).pack(side="left", padx=4)
        
        # Bind search
        self.lib_search_var.trace_add("write", lambda *args: self.filter_library_browser())
        
        # Auto-load if library path is set
        if self.personal.get():
            self.after(100, self.refresh_library_browser)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 3D MODELS TAB
    # ═══════════════════════════════════════════════════════════════════════
    def _create_3d_models_tab(self, parent):
        """3D Models tab — validate every footprint's 3D path, find missing
        models, auto-fix backslash corruption, and provide a manual search
        for the stragglers."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)
        
        # ---- Top toolbar (search + filter + scan) ----
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        top.grid_columnconfigure(1, weight=1)
        
        ttk.Label(top, text="🔍 Search:").grid(row=0, column=0, padx=(0, 8))
        self._3d_search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._3d_search_var, font=("Segoe UI", 10)
                 ).grid(row=0, column=1, sticky="ew", padx=4)
        
        ttk.Label(top, text="Show:").grid(row=0, column=2, padx=(12, 4))
        self._3d_filter_var = tk.StringVar(value="All")
        filter_cb = ttk.Combobox(
            top, textvariable=self._3d_filter_var, width=26, state="readonly",
            values=[
                "All",
                "Issues only (everything except OK)",
                "✅ OK",
                "🔧 Corrupted (backslash)",
                "❌ Missing file",
                "❓ No path",
                "🔍 Suggested by search",
                "⚠️ Not found",
            ],
        )
        filter_cb.grid(row=0, column=3, padx=4)
        
        ttk.Button(top, text="🔄 Scan", command=self.scan_3d_models_tab,
                   style="Accent.TButton").grid(row=0, column=4, padx=4)
        
        self._3d_search_var.trace_add("write", lambda *a: self._refresh_3d_tree())
        self._3d_filter_var.trace_add("write", lambda *a: self._refresh_3d_tree())
        
        # ---- Summary banner ----
        banner = ttk.Frame(parent)
        banner.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._3d_summary_var = tk.StringVar(
            value="Click 'Scan' to inspect every footprint in the library."
        )
        ttk.Label(banner, textvariable=self._3d_summary_var,
                  foreground=COLORS['text_muted'],
                  background=COLORS['panel'], padding=8,
                  relief="solid", borderwidth=1).pack(fill="x")
        
        # ---- Instructions ----
        instr = ttk.Frame(parent)
        instr.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        ttk.Label(
            instr,
            text=("📋  No files are modified during scan."
                  "    Use 'Fix Corrupted Paths…' to repair backslash damage (preview first)."
                  "    Double-click any row to manually search for a model."
                  "    Tick rows and 'Apply Suggested' to accept search matches."),
            foreground=COLORS['accent'], font=("Segoe UI", 9, "bold"),
            background=COLORS['panel'], padding=8, relief="solid", borderwidth=1,
        ).pack(fill="x")
        
        # ---- Main tree ----
        tree_frame = ttk.Frame(parent)
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 8))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        
        cols = ("status", "footprint", "current_path", "resolved", "suggested", "confidence")
        self._3d_tree = ttk.Treeview(tree_frame, columns=cols, show="tree headings",
                                     selectmode="extended")
        self._3d_tree.grid(row=0, column=0, sticky="nsew")
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._3d_tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._3d_tree.configure(yscrollcommand=vsb.set)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._3d_tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self._3d_tree.configure(xscrollcommand=hsb.set)
        
        self._3d_tree.heading("#0", text="☐")
        self._3d_tree.heading("status", text="Status")
        self._3d_tree.heading("footprint", text="Footprint")
        self._3d_tree.heading("current_path", text="Current Path")
        self._3d_tree.heading("resolved", text="Resolves To")
        self._3d_tree.heading("suggested", text="Suggested 3D Model")
        self._3d_tree.heading("confidence", text="Confidence")
        
        self._3d_tree.column("#0", width=44, anchor="center", stretch=False)
        self._3d_tree.column("status", width=140, anchor="w", stretch=False)
        self._3d_tree.column("footprint", width=220, anchor="w")
        self._3d_tree.column("current_path", width=300, anchor="w")
        self._3d_tree.column("resolved", width=180, anchor="w")
        self._3d_tree.column("suggested", width=220, anchor="w")
        self._3d_tree.column("confidence", width=90, anchor="center", stretch=False)
        
        self._3d_tree.tag_configure("ok", foreground=COLORS['success'])
        self._3d_tree.tag_configure("missing", foreground=COLORS['error'])
        self._3d_tree.tag_configure("nopath", foreground=COLORS['warning'])
        self._3d_tree.tag_configure("corrupted", foreground=COLORS['warning'],
                                    font=("Segoe UI", 10, "bold"))
        self._3d_tree.tag_configure("found_exact", foreground=COLORS['success'],
                                    font=("Segoe UI", 10, "bold"))
        self._3d_tree.tag_configure("found_fuzzy", foreground=COLORS['accent'])
        self._3d_tree.tag_configure("notfound", foreground=COLORS['error'],
                                    font=("Segoe UI", 10, "bold"))
        self._3d_tree.tag_configure("error", foreground=COLORS['error'])
        
        self._3d_tree.bind("<Button-1>", self._on_3d_tree_click)
        self._3d_tree.bind("<Double-1>", self._on_3d_tree_double_click)
        
        # ---- Action button row ----
        actions = ttk.Frame(parent)
        actions.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))
        
        ttk.Button(actions, text="Select All Suggested",
                   command=lambda: self._3d_select_all(only_suggested=True)
                   ).pack(side="left", padx=2)
        ttk.Button(actions, text="Select All",
                   command=lambda: self._3d_select_all(only_suggested=False)
                   ).pack(side="left", padx=2)
        ttk.Button(actions, text="Select None",
                   command=self._3d_select_none).pack(side="left", padx=2)
        
        ttk.Button(actions, text="🔎 Manual Search…",
                   command=self._3d_manual_search_for_selection
                   ).pack(side="left", padx=12)
        
        # Disabled until a scan finds corrupted paths
        self._3d_fix_corrupted_btn = ttk.Button(
            actions, text="🔧 Fix Corrupted Paths",
            command=self._3d_fix_corrupted_paths_dialog,
            state="disabled",
        )
        self._3d_fix_corrupted_btn.pack(side="left", padx=4)
        
        ttk.Button(actions, text="✅ Apply Suggested to Checked",
                   command=self._3d_apply_suggested,
                   style="Accent.TButton"
                   ).pack(side="right", padx=4, ipadx=12, ipady=4)
        
        # State
        self._3d_results = []           # list[dict] from analyze_footprint_3d
        self._3d_checked = set()        # set[fp_name] of ticked rows
        self._3d_manual_picks = {}      # fp_name -> Path (manually chosen)
        self._3d_corrupted_count = 0
        self._3d_tab_first_visit_done = False
    
    # ---- 3D Models tab logic ----
    def scan_3d_models_tab(self):
        """Walk every footprint, analyze its 3D status, populate the tree.

        Does NOT modify any files -- corrupted backslash paths are reported
        but only repaired when the user clicks 'Fix Corrupted Paths…'.
        """
        fp_dir = self.current_fp_dir()
        models_root = self.current_models_root()
        personal_root = Path(self.personal.get()).expanduser()
        
        if not fp_dir.exists():
            self._3d_summary_var.set(
                f"⚠️  Footprint directory not found: {fp_dir}"
            )
            return
        
        # Clear tree + state
        for item in self._3d_tree.get_children():
            self._3d_tree.delete(item)
        self._3d_results = []
        self._3d_checked.clear()
        # Note: keep self._3d_manual_picks across scans so that picks made
        # before applying don't vanish if the user re-scans.
        
        self._3d_summary_var.set("Scanning footprints…")
        self.update_idletasks()
        
        # Build models index once per scan
        models_index = _available_models_index(models_root) if models_root.exists() \
                       else ({}, {}, {}, [])
        
        fps = sorted(fp_dir.glob("*.kicad_mod"), key=lambda p: p.name.lower())
        for fp in fps:
            info = analyze_footprint_3d(
                fp, models_root, personal_root,
                models_index=models_index, prefer_ext="step",
            )
            self._3d_results.append(info)
        
        self._refresh_3d_tree()
        
        # Summary -- count by effective bucket (the same bucketing used by
        # _refresh_3d_tree's filter logic, kept in sync below).
        n_ok = n_missing_unmatched = n_nopath_unmatched = 0
        n_found_exact = n_found_fuzzy = n_corrupted = n_error = 0
        for info in self._3d_results:
            if info.get("corrupted"):
                n_corrupted += 1
            st = info["status"]
            has_sug = info.get("suggested") is not None
            if st == "error":
                n_error += 1
            elif st == "ok":
                n_ok += 1
            elif st == "missing":
                if has_sug:
                    if info["suggested_method"] in ("exact", "canon"):
                        n_found_exact += 1
                    else:
                        n_found_fuzzy += 1
                else:
                    n_missing_unmatched += 1
            elif st == "no_path":
                if has_sug:
                    if info["suggested_method"] in ("exact", "canon"):
                        n_found_exact += 1
                    else:
                        n_found_fuzzy += 1
                else:
                    n_nopath_unmatched += 1
        
        # Cache for the Fix-Corrupted dialog button-state
        self._3d_corrupted_count = n_corrupted
        # Update the button (created later) if it exists
        if hasattr(self, "_3d_fix_corrupted_btn"):
            try:
                if n_corrupted:
                    self._3d_fix_corrupted_btn.config(
                        text=f"🔧 Fix Corrupted Paths ({n_corrupted})…",
                        state="normal",
                    )
                else:
                    self._3d_fix_corrupted_btn.config(
                        text="🔧 Fix Corrupted Paths",
                        state="disabled",
                    )
            except Exception:
                pass
        
        msg_bits = [f"Scanned {len(self._3d_results)} footprints"]
        msg_bits.append(f"✅ {n_ok} OK")
        if n_corrupted:
            msg_bits.append(f"🔧 {n_corrupted} corrupted (click 'Fix Corrupted Paths…')")
        if n_missing_unmatched:
            msg_bits.append(f"❌ {n_missing_unmatched} missing")
        if n_nopath_unmatched:
            msg_bits.append(f"❓ {n_nopath_unmatched} no path")
        if n_found_exact:
            msg_bits.append(f"🎯 {n_found_exact} exact match suggested")
        if n_found_fuzzy:
            msg_bits.append(f"🔍 {n_found_fuzzy} fuzzy match suggested")
        if n_error:
            msg_bits.append(f"⚠️ {n_error} errors")
        self._3d_summary_var.set("   |   ".join(msg_bits))
    
    def _refresh_3d_tree(self):
        """Re-render the tree from self._3d_results, applying search + filter."""
        if not hasattr(self, "_3d_tree"):
            return
        for item in self._3d_tree.get_children():
            self._3d_tree.delete(item)
        
        query = self._3d_search_var.get().lower().strip()
        flt = self._3d_filter_var.get()
        
        for info in self._3d_results:
            fp_name = info["fp_name"]
            if query and query not in fp_name.lower():
                cur = info.get("raw_path", "") or ""
                sug = info.get("suggested")
                sug_name = sug.name if sug else ""
                if query not in cur.lower() and query not in sug_name.lower():
                    continue
            
            status = info["status"]
            corrupted = bool(info.get("corrupted"))
            has_suggestion = info.get("suggested") is not None
            
            # Apply filter
            if flt == "Issues only (everything except OK)":
                if status == "ok" and not corrupted:
                    continue
            elif flt == "✅ OK":
                if not (status == "ok" and not corrupted):
                    continue
            elif flt == "🔧 Corrupted (backslash)":
                if not corrupted:
                    continue
            elif flt == "❌ Missing file":
                if not (status == "missing" and not has_suggestion):
                    continue
            elif flt == "❓ No path":
                if not (status == "no_path" and not has_suggestion):
                    continue
            elif flt == "🔍 Suggested by search":
                if not has_suggestion:
                    continue
            elif flt == "⚠️ Not found":
                # Anything where there's a problem but no suggestion to apply
                ok_or_corrupted_only = (status == "ok") or (corrupted and status != "missing" and status != "no_path")
                if ok_or_corrupted_only or has_suggestion:
                    continue
            
            # Status string + tag
            if fp_name in self._3d_manual_picks:
                status_text = "🎯 Manual pick"
                tag = "found_exact"
            elif corrupted and status == "ok":
                # Path resolves (after backslash normalization), but the file
                # on disk still has the corrupt path -- needs cleanup.
                status_text = "🔧 Corrupted path (file OK)"
                tag = "corrupted"
            elif corrupted and status == "missing":
                if has_suggestion:
                    status_text = "🔧 Corrupted + 🔍 match found"
                    tag = "corrupted"
                else:
                    status_text = "🔧 Corrupted + ❌ missing"
                    tag = "corrupted"
            elif status == "ok":
                status_text = "✅ OK"
                tag = "ok"
            elif status == "missing" and has_suggestion:
                if info["suggested_method"] in ("exact", "canon"):
                    status_text = "❌ Missing → 🎯 exact match found"
                    tag = "found_exact"
                else:
                    status_text = "❌ Missing → 🔍 fuzzy match"
                    tag = "found_fuzzy"
            elif status == "missing":
                status_text = "❌ Missing file"
                tag = "missing"
            elif status == "no_path" and has_suggestion:
                if info["suggested_method"] in ("exact", "canon"):
                    status_text = "❓ No path → 🎯 exact match found"
                    tag = "found_exact"
                else:
                    status_text = "❓ No path → 🔍 fuzzy match"
                    tag = "found_fuzzy"
            elif status == "no_path":
                status_text = "❓ No path / not found"
                tag = "notfound"
            elif status == "error":
                status_text = f"⚠️ Error: {info.get('error','')[:30]}"
                tag = "error"
            else:
                status_text = status
                tag = ""
            
            cur = info.get("raw_path", "") or "(none)"
            resolved = ""
            if info.get("resolved") is not None:
                resolved = info["resolved"].name if info.get("file_exists") else "(missing)"
            
            sug = self._3d_manual_picks.get(fp_name) or info.get("suggested")
            sug_text = sug.name if sug else ""
            conf = ""
            if fp_name in self._3d_manual_picks:
                conf = "manual"
            elif info.get("suggested") is not None:
                pct = int(round(info["suggested_confidence"] * 100))
                method = info["suggested_method"]
                conf = f"{pct}% {method}"
            
            checkbox = "☑" if fp_name in self._3d_checked else "☐"
            self._3d_tree.insert(
                "", "end", text=checkbox,
                values=(status_text, fp_name, cur, resolved, sug_text, conf),
                tags=(tag,),
            )
    
    def _on_3d_tree_click(self, event):
        """Toggle the checkbox if the user clicked on the tree (#0) column."""
        region = self._3d_tree.identify_region(event.x, event.y)
        if region != "tree":
            return
        item = self._3d_tree.identify_row(event.y)
        if not item:
            return
        values = self._3d_tree.item(item, "values")
        if not values:
            return
        fp_name = values[1]
        if fp_name in self._3d_checked:
            self._3d_checked.remove(fp_name)
            self._3d_tree.item(item, text="☐")
        else:
            self._3d_checked.add(fp_name)
            self._3d_tree.item(item, text="☑")
    
    def _on_3d_tree_double_click(self, event):
        """Open the manual search dialog for the row under the cursor."""
        region = self._3d_tree.identify_region(event.x, event.y)
        # For a double-click we want to open the dialog regardless of
        # which column the click landed in (except on the #0 column,
        # which is already handled by the single-click toggle and would
        # otherwise lead to a confusing combined toggle+open).
        if region == "tree":
            return
        item = self._3d_tree.identify_row(event.y)
        if not item:
            return
        values = self._3d_tree.item(item, "values")
        if not values:
            return
        fp_name = values[1]
        self._3d_open_manual_search(fp_name)
    
    def _3d_select_all(self, only_suggested: bool = False):
        for info in self._3d_results:
            fp_name = info["fp_name"]
            if only_suggested:
                # Tick rows that have either a manual pick or an auto-suggestion
                has_pick = fp_name in self._3d_manual_picks
                has_sug = info.get("suggested") is not None
                if has_pick or has_sug:
                    self._3d_checked.add(fp_name)
            else:
                self._3d_checked.add(fp_name)
        self._refresh_3d_tree()
    
    def _3d_select_none(self):
        self._3d_checked.clear()
        self._refresh_3d_tree()
    
    def _3d_manual_search_for_selection(self):
        """Open the manual search dialog for the currently focused row."""
        sel = self._3d_tree.selection()
        if not sel:
            messagebox.showinfo(
                "No selection",
                "Click a row to highlight it, then use Manual Search "
                "(or just double-click the row).",
            )
            return
        values = self._3d_tree.item(sel[0], "values")
        if not values:
            return
        self._3d_open_manual_search(values[1])
    
    def _3d_open_manual_search(self, fp_name: str):
        """Modal: search the models tree (or browse anywhere) and pick a model."""
        models_root = self.current_models_root()
        fp_dir = self.current_fp_dir()
        
        win = tk.Toplevel(self)
        win.title(f"Find 3D Model — {fp_name}")
        win.geometry("900x650")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        win.grab_set()
        
        # Header
        head = ttk.Frame(win)
        head.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(head, text=f"Find 3D model for: {fp_name}",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(head, text=f"Models root: {models_root}",
                  foreground=COLORS['text_muted']).pack(anchor="w", pady=(2, 0))
        
        # Search entry
        srow = ttk.Frame(win)
        srow.pack(fill="x", padx=12, pady=(8, 4))
        ttk.Label(srow, text="🔍 Filter:").pack(side="left", padx=(0, 8))
        search_var = tk.StringVar(value=fp_name)  # pre-fill with footprint name
        entry = ttk.Entry(srow, textvariable=search_var, font=("Segoe UI", 10))
        entry.pack(side="left", fill="x", expand=True)
        
        # Tree of models found
        tree_frame = ttk.Labelframe(win, text="Models in library directory", padding=8)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=4)
        
        cols = ("name", "type", "score", "path")
        mtree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        mtree.pack(side="left", fill="both", expand=True)
        mvsb = ttk.Scrollbar(tree_frame, orient="vertical", command=mtree.yview)
        mvsb.pack(side="right", fill="y")
        mtree.configure(yscrollcommand=mvsb.set)
        
        mtree.heading("name", text="Model name")
        mtree.heading("type", text="Type")
        mtree.heading("score", text="Match")
        mtree.heading("path", text="Relative path")
        mtree.column("name", width=240, anchor="w")
        mtree.column("type", width=70, anchor="center")
        mtree.column("score", width=80, anchor="center")
        mtree.column("path", width=380, anchor="w")
        
        # Build full list once, then filter against it
        all_models = []
        if models_root.exists():
            for f in models_root.rglob("*"):
                if f.is_file() and f.suffix.lower() in (".step", ".stp", ".wrl"):
                    all_models.append(f)
        
        fp_canon = _canon(fp_name)
        
        def populate(filter_text=""):
            for it in mtree.get_children():
                mtree.delete(it)
            ft = filter_text.lower().strip()
            
            scored = []
            for m in all_models:
                stem_lower = m.stem.lower()
                if ft and ft not in stem_lower and ft not in str(m).lower():
                    continue
                # Score against the footprint name so the closest match floats up
                s = _score(_canon(m.stem), fp_canon)
                scored.append((s, m))
            
            # Highest score first
            scored.sort(key=lambda x: (-x[0], x[1].name.lower()))
            
            for s, m in scored[:500]:  # cap to keep the UI snappy
                mtype = "STEP" if m.suffix.lower() in (".step", ".stp") else "WRL"
                try:
                    rel = m.relative_to(models_root)
                except Exception:
                    rel = m
                pct = f"{int(round(s * 100))}%" if s > 0 else ""
                mtree.insert("", "end", values=(m.stem, mtype, pct, str(rel)),
                             tags=(str(m),))
            
            # Auto-select top result
            kids = mtree.get_children()
            if kids:
                mtree.selection_set(kids[0])
                mtree.see(kids[0])
        
        search_var.trace_add("write", lambda *a: populate(search_var.get()))
        populate(search_var.get())
        
        # Currently-selected file label (for the Browse button)
        chosen = {"path": None}
        chosen_var = tk.StringVar(value="(no file chosen)")
        chosen_row = ttk.Frame(win)
        chosen_row.pack(fill="x", padx=12, pady=(4, 0))
        ttk.Label(chosen_row, text="Selected:", foreground=COLORS['text_muted']
                  ).pack(side="left")
        ttk.Label(chosen_row, textvariable=chosen_var, foreground=COLORS['accent']
                  ).pack(side="left", padx=8)
        
        def update_chosen_from_tree(*_):
            sel = mtree.selection()
            if not sel:
                return
            tags = mtree.item(sel[0], "tags")
            if tags:
                p = Path(tags[0])
                chosen["path"] = p
                chosen_var.set(p.name)
        
        mtree.bind("<<TreeviewSelect>>", update_chosen_from_tree)
        update_chosen_from_tree()
        
        # Buttons
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=12)
        
        def do_browse():
            init = models_root if models_root.exists() else Path.home()
            f = filedialog.askopenfilename(
                title="Browse for 3D model",
                initialdir=str(init),
                filetypes=[
                    ("3D models", "*.step *.stp *.wrl"),
                    ("STEP", "*.step *.stp"),
                    ("VRML", "*.wrl"),
                    ("All files", "*.*"),
                ],
            )
            if f:
                p = Path(f)
                chosen["path"] = p
                chosen_var.set(p.name)
        
        def do_use():
            if chosen["path"] is None:
                messagebox.showwarning(
                    "No selection",
                    "Pick a model from the list, or click 'Browse' to choose any file.",
                )
                return
            self._3d_manual_picks[fp_name] = chosen["path"]
            self._3d_checked.add(fp_name)
            # Patch the in-memory result so the row updates on refresh
            for info in self._3d_results:
                if info["fp_name"] == fp_name:
                    info["suggested"] = chosen["path"]
                    info["suggested_confidence"] = 1.0
                    info["suggested_method"] = "manual"
                    break
            self._refresh_3d_tree()
            win.destroy()
        
        def do_clear():
            if fp_name in self._3d_manual_picks:
                del self._3d_manual_picks[fp_name]
            self._refresh_3d_tree()
            win.destroy()
        
        ttk.Button(btns, text="📁 Browse for file…", command=do_browse
                   ).pack(side="left", padx=4)
        ttk.Button(btns, text="🔄 Clear manual pick", command=do_clear
                   ).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=win.destroy
                   ).pack(side="right", padx=4)
        ttk.Button(btns, text="✅ Use selected model", command=do_use,
                   style="Accent.TButton"
                   ).pack(side="right", padx=4, ipadx=10, ipady=4)
        
        # Double-click any row in the tree to choose it
        mtree.bind("<Double-1>", lambda e: (update_chosen_from_tree(), do_use()))
        
        # Focus on entry (text already pre-selected) so the user can type
        # over it immediately if they want a different query.
        entry.focus_set()
        entry.select_range(0, "end")
    
    def _3d_fix_corrupted_paths_dialog(self):
        """Preview backslash-corrupted (model "...") paths, let the user
        confirm, repair them, then show a summary of what was changed."""
        corrupted = [info for info in self._3d_results if info.get("corrupted")]
        if not corrupted:
            messagebox.showinfo(
                "Nothing to fix",
                "No corrupted backslash paths were found in the current scan.",
            )
            return
        
        win = tk.Toplevel(self)
        win.title(f"Fix Corrupted 3D Paths — {len(corrupted)} footprints")
        win.geometry("1100x650")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        win.grab_set()
        
        # Header
        head = ttk.Frame(win)
        head.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(head, text=f"Found {len(corrupted)} footprint(s) with backslash-corrupted 3D paths",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            head,
            text=("Backslashes inside (model \"…\") strings get interpreted as escape "
                  "sequences by KiCad's parser (\\3 → chr(3), invisible), which is "
                  "why your paths displayed as '..d\\step\\…'. Fixing converts \\ "
                  "to / and preserves all rotation / scale / offset transforms."),
            foreground=COLORS['text_muted'], wraplength=1050, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        
        # Tree of corrupted footprints with current/proposed path
        tree_frame = ttk.Labelframe(win, text="Preview of changes", padding=8)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=8)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        
        cols = ("footprint", "current", "fixed")
        ftree = ttk.Treeview(tree_frame, columns=cols, show="tree headings", selectmode="extended")
        ftree.grid(row=0, column=0, sticky="nsew")
        fvsb = ttk.Scrollbar(tree_frame, orient="vertical", command=ftree.yview)
        fvsb.grid(row=0, column=1, sticky="ns")
        ftree.configure(yscrollcommand=fvsb.set)
        fhsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=ftree.xview)
        fhsb.grid(row=1, column=0, sticky="ew")
        ftree.configure(xscrollcommand=fhsb.set)
        
        ftree.heading("#0", text="☑")
        ftree.heading("footprint", text="Footprint")
        ftree.heading("current", text="Current path (broken)")
        ftree.heading("fixed", text="Proposed path (fixed)")
        ftree.column("#0", width=44, anchor="center", stretch=False)
        ftree.column("footprint", width=220, anchor="w")
        ftree.column("current", width=380, anchor="w")
        ftree.column("fixed", width=380, anchor="w")
        
        ftree.tag_configure("bad", foreground=COLORS['warning'])
        
        # All ticked by default; the user can untick any row to skip it
        ticked = {}
        for info in corrupted:
            cur = info.get("raw_path", "") or ""
            fixed = cur.replace("\\", "/")
            iid = ftree.insert("", "end", text="☑",
                               values=(info["fp_name"], cur, fixed),
                               tags=("bad",))
            ticked[iid] = info
        
        def on_click(event):
            region = ftree.identify_region(event.x, event.y)
            if region != "tree":
                return
            iid = ftree.identify_row(event.y)
            if not iid or iid not in ticked:
                return
            cur_text = ftree.item(iid, "text")
            ftree.item(iid, text=("☐" if cur_text == "☑" else "☑"))
        
        ftree.bind("<Button-1>", on_click)
        
        # Helper: tick / untick all
        def set_all(value):
            for iid in ticked:
                ftree.item(iid, text=value)
        
        controls = ttk.Frame(win)
        controls.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Button(controls, text="Select All", command=lambda: set_all("☑")
                   ).pack(side="left", padx=2)
        ttk.Button(controls, text="Select None", command=lambda: set_all("☐")
                   ).pack(side="left", padx=2)
        ttk.Label(controls, text="(Footprints with a \\ in the (model \"…\") path)",
                  foreground=COLORS['text_muted']).pack(side="left", padx=12)
        
        # Apply / cancel
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=12)
        
        def do_apply():
            chosen = [info for iid, info in ticked.items()
                      if ftree.item(iid, "text") == "☑"]
            if not chosen:
                messagebox.showwarning(
                    "Nothing ticked",
                    "Tick at least one row to fix, or click Cancel.",
                    parent=win,
                )
                return
            if not messagebox.askyesno(
                "Confirm",
                f"Fix backslashes in {len(chosen)} footprint(s)?\n\n"
                "Each file is rewritten in place. Rotations / scales / "
                "offsets are preserved.",
                parent=win,
            ):
                return
            
            # Apply
            fixed_names = []
            unchanged_names = []
            errors = []
            for info in chosen:
                fp = info["fp_path"]
                try:
                    n = fix_corrupted_model_paths(fp)
                    if n > 0:
                        fixed_names.append((info["fp_name"], n))
                    else:
                        unchanged_names.append(info["fp_name"])
                except Exception as e:
                    errors.append(f"{info['fp_name']}: {e}")
            
            win.destroy()
            
            # Re-scan so the UI reflects the new state on disk
            self.scan_3d_models_tab()
            
            # Summary popup
            self._show_fix_corrupted_summary(fixed_names, unchanged_names, errors)
        
        ttk.Button(btns, text="Cancel", command=win.destroy
                   ).pack(side="right", padx=4)
        ttk.Button(btns, text="✅ Fix Ticked Footprints",
                   command=do_apply, style="Accent.TButton"
                   ).pack(side="right", padx=4, ipadx=10, ipady=4)
    
    def _show_fix_corrupted_summary(self, fixed, unchanged, errors):
        """Modal summary after the user repairs corrupted paths."""
        win = tk.Toplevel(self)
        win.title("Path Fix Summary")
        win.geometry("700x500")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        win.grab_set()
        
        head = ttk.Frame(win)
        head.pack(fill="x", padx=12, pady=12)
        if not errors:
            ttk.Label(head, text="✅ Done", font=("Segoe UI", 14, "bold"),
                      foreground=COLORS['success']).pack(anchor="w")
        else:
            ttk.Label(head, text="⚠ Done with errors", font=("Segoe UI", 14, "bold"),
                      foreground=COLORS['warning']).pack(anchor="w")
        
        line = (f"Fixed {len(fixed)} footprint(s)"
                + (f", {len(unchanged)} unchanged" if unchanged else "")
                + (f", {len(errors)} error(s)" if errors else ""))
        ttk.Label(head, text=line, foreground=COLORS['text_muted']
                  ).pack(anchor="w", pady=(4, 0))
        
        body = ScrolledText(win, wrap="word", bg=COLORS['panel'],
                            fg=COLORS['text'], font=("Consolas", 10))
        body.pack(fill="both", expand=True, padx=12, pady=8)
        
        if fixed:
            body.insert("end", f"📝 Fixed ({len(fixed)})\n", "h")
            for name, n in fixed:
                tail = f"  ({n} path{'s' if n != 1 else ''} cleaned)" if n != 1 else ""
                body.insert("end", f"   • {name}{tail}\n")
            body.insert("end", "\n")
        if unchanged:
            body.insert("end", f"➖ Unchanged ({len(unchanged)})\n", "h")
            for name in unchanged:
                body.insert("end", f"   • {name}\n")
            body.insert("end", "\n")
        if errors:
            body.insert("end", f"❌ Errors ({len(errors)})\n", "h")
            for err in errors:
                body.insert("end", f"   • {err}\n")
        
        body.tag_configure("h", font=("Segoe UI", 11, "bold"),
                           foreground=COLORS['accent'])
        body.configure(state="disabled")
        
        ttk.Button(win, text="Close", command=win.destroy,
                   style="Accent.TButton"
                   ).pack(pady=(0, 12), ipadx=20, ipady=4)
    
    def _3d_apply_suggested(self):
        """Write the suggested (or manually picked) model into each checked
        footprint. Corrupted backslash paths are handled separately by
        'Fix Corrupted Paths…' so this step is purely a search-and-replace."""
        if not self._3d_checked:
            messagebox.showwarning(
                "Nothing selected",
                "Tick at least one row, then click Apply. Tip: use "
                "'Select All Suggested' to pick everything that has a match.",
            )
            return
        
        fp_dir = self.current_fp_dir()
        personal_root = Path(self.personal.get()).expanduser()
        use_relative = bool(self.rel_models.get())
        
        # Build worklist: fp_name -> Path
        work = []
        skipped_no_match = []
        already_ok = []
        for info in self._3d_results:
            fp_name = info["fp_name"]
            if fp_name not in self._3d_checked:
                continue
            chosen = self._3d_manual_picks.get(fp_name) or info.get("suggested")
            if chosen is None:
                skipped_no_match.append(fp_name)
                continue
            if info["status"] == "ok" and not self._3d_manual_picks.get(fp_name):
                # Already pointing at a real file, nothing to do unless the
                # user explicitly chose a different model manually.
                already_ok.append(fp_name)
                continue
            work.append((fp_name, chosen))
        
        if not work:
            msg = "Nothing to apply.\n\n"
            if skipped_no_match:
                msg += f"• {len(skipped_no_match)} checked row(s) have no matching model — use Manual Search.\n"
            if already_ok:
                msg += f"• {len(already_ok)} checked row(s) are already OK.\n"
            messagebox.showinfo("Nothing to apply", msg)
            return
        
        if not messagebox.askyesno(
            "Confirm",
            f"Update 3D model paths in {len(work)} footprint(s)?\n\n"
            f"• Path format: {'relative' if use_relative else '${PERSONAL_LIB_DIR}'}\n"
            f"• Existing transforms (rotate/scale/offset) are preserved.\n"
            f"• {len(self._3d_manual_picks)} row(s) use a manual pick; the rest use the auto-suggestion.",
        ):
            return
        
        updated, errors = 0, []
        for fp_name, chosen_path in work:
            fp_path = fp_dir / f"{fp_name}.kicad_mod"
            if not fp_path.exists():
                errors.append(f"{fp_name}: file vanished")
                continue
            try:
                if replace_model_path_in_footprint(
                    fp_path, chosen_path, fp_dir, personal_root,
                    use_relative=use_relative,
                ):
                    updated += 1
            except Exception as e:
                errors.append(f"{fp_name}: {e}")
        
        # Clear manual picks that were just applied
        for fp_name, _ in work:
            self._3d_manual_picks.pop(fp_name, None)
        self._3d_checked.clear()
        
        # Re-scan to reflect what's now on disk
        self.scan_3d_models_tab()
        
        msg = f"Updated {updated} footprint(s)."
        if skipped_no_match:
            msg += f"\nSkipped (no match): {len(skipped_no_match)}"
        if already_ok:
            msg += f"\nSkipped (already OK): {len(already_ok)}"
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors[:8])
            if len(errors) > 8:
                msg += f"\n…and {len(errors) - 8} more"
            messagebox.showwarning("Done with issues", msg)
        else:
            messagebox.showinfo("Done", msg)
    
    def _create_bom_manager_tab(self, parent):
        """Create BOM Manager tab for importing Octopart BOMs and updating schematics."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)
        
        # File selection section
        file_frame = ttk.Labelframe(parent, text="1. Select Files", padding=12)
        file_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16,8))
        file_frame.grid_columnconfigure(1, weight=1)
        
        # Octopart BOM file
        ttk.Label(file_frame, text="Octopart BOM:").grid(row=0, column=0, sticky="w", padx=(0,8), pady=4)
        self.bom_file_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.bom_file_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(file_frame, text="📁 Browse", command=self.browse_bom_file).grid(row=0, column=2, padx=4, pady=4)
        
        # KiCad schematic file
        ttk.Label(file_frame, text="KiCad Schematic:").grid(row=1, column=0, sticky="w", padx=(0,8), pady=4)
        self.sch_file_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.sch_file_var, state="readonly").grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(file_frame, text="📁 Browse", command=self.browse_sch_file).grid(row=1, column=2, padx=4, pady=4)
        
        # Load button
        btn_load = ttk.Button(file_frame, text="🔄 Load & Analyze", command=self.load_bom_files, style="Accent.TButton")
        btn_load.grid(row=2, column=0, columnspan=3, pady=(8,0))
        
        # Status section
        status_frame = ttk.Frame(parent)
        status_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        self.bom_status_var = tk.StringVar(value="Select files and click 'Load & Analyze' to begin...")
        ttk.Label(status_frame, textvariable=self.bom_status_var, foreground=COLORS['text_muted']).pack(side="left")
        
        # Preview tree
        preview_frame = ttk.Labelframe(parent, text="2. Preview Changes", padding=12)
        preview_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(0, weight=1)
        
        cols = ("ref", "value", "footprint", "current_mpn", "new_mpn", "action", "file")
        self.bom_tree = ttk.Treeview(preview_frame, columns=cols, show="headings", height=15)
        self.bom_tree.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbars
        vsb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.bom_tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.bom_tree.configure(yscrollcommand=vsb.set)
        
        hsb = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.bom_tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.bom_tree.configure(xscrollcommand=hsb.set)
        
        # Configure columns
        self.bom_tree.heading("ref", text="Reference")
        self.bom_tree.heading("value", text="Value")
        self.bom_tree.heading("footprint", text="Footprint")
        self.bom_tree.heading("current_mpn", text="Current MPN")
        self.bom_tree.heading("new_mpn", text="New MPN")
        self.bom_tree.heading("action", text="Action")
        self.bom_tree.heading("file", text="File")
        
        self.bom_tree.column("ref", width=100, anchor="w")
        self.bom_tree.column("value", width=100, anchor="w")
        self.bom_tree.column("footprint", width=180, anchor="w")
        self.bom_tree.column("current_mpn", width=150, anchor="w")
        self.bom_tree.column("new_mpn", width=150, anchor="w")
        self.bom_tree.column("action", width=100, anchor="center")
        self.bom_tree.column("file", width=150, anchor="w")
        
        # Action buttons
        action_frame = ttk.Frame(parent)
        action_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(8,16))
        
        self.bom_summary_var = tk.StringVar(value="")
        ttk.Label(action_frame, textvariable=self.bom_summary_var, foreground=COLORS['text_muted']).pack(side="left", padx=8)
        
        ttk.Button(action_frame, text="✅ Apply to Schematics", command=self.apply_bom_updates, style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(action_frame, text="📤 Export Preview CSV", command=self.export_bom_preview).pack(side="right", padx=4)
    
    def _create_settings_tab(self, parent):
        """Create settings tab."""
        parent.grid_columnconfigure(0, weight=1)
        
        r = 0
        
        # General Settings
        lf = ttk.Labelframe(parent, text="General Settings", padding=16)
        lf.grid(row=r, column=0, sticky="ew", padx=16, pady=(16,8)); r += 1
        lf.grid_columnconfigure(1, weight=1)
        
        ttk.Label(lf, text="Footprint Nickname:").grid(row=0, column=0, sticky="e", padx=(0,8), pady=6)
        nick_row = ttk.Frame(lf)
        nick_row.grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        nick_row.grid_columnconfigure(0, weight=1)
        nick_entry = ttk.Entry(nick_row, textvariable=self.nick)
        nick_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            nick_row, text="Auto-detect", variable=self.auto_nick,
            command=lambda: self.detect_and_set_nickname(force=True) if self.auto_nick.get() else None
        ).grid(row=0, column=1, padx=(8,0))
        ttk.Button(
            nick_row, text="Detect",
            command=lambda: self.detect_and_set_nickname(force=True)
        ).grid(row=0, column=2, padx=(6,0))
        # Physical typing means the user wants a manual value -- turn auto off so it
        # sticks. Programmatic .set() does not fire KeyRelease, so detection is safe.
        nick_entry.bind("<KeyRelease>", lambda e: self.auto_nick.set(False))
        
        ttk.Label(lf, text="Deduplication Mode:").grid(row=1, column=0, sticky="e", padx=(0,8), pady=6)
        ttk.Combobox(lf, textvariable=self.dedupe_mode, 
                    values=["Ignore Properties", "Exact", "Pins-only"]).grid(row=1, column=1, sticky="ew", padx=4, pady=6)
        
        ttk.Label(lf, text="Prefer Package Type:").grid(row=2, column=0, sticky="e", padx=(0,8), pady=6)
        ttk.Combobox(lf, textvariable=self.prefer_pkg, 
                    values=["folder", "zip"]).grid(row=2, column=1, sticky="ew", padx=4, pady=6)
        
        # KiCad Paths
        lf2 = ttk.Labelframe(parent, text="KiCad Path Variables", padding=16)
        lf2.grid(row=r, column=0, sticky="ew", padx=16, pady=8); r += 1
        lf2.grid_columnconfigure(0, weight=1)
        
        ttk.Button(lf2, text="Set ${PERSONAL_LIB_DIR}", command=self.set_kicad_var).pack(fill="x", pady=4)
        ttk.Button(lf2, text="Rewrite fp-lib-table", command=self.rewrite_fp_table).pack(fill="x", pady=4)
        ttk.Button(lf2, text="Rewrite 3D Model Paths", command=self.rewrite_models).pack(fill="x", pady=4)
        
        # About section
        lf3 = ttk.Labelframe(parent, text="About", padding=16)
        lf3.grid(row=r, column=0, sticky="ew", padx=16, pady=8); r += 1
        
        about_text = f"""{APP_NAME} v{APP_VERSION}
        
Modern, portable library manager for KiCad.
No external dependencies required.

Features bug fixes and UI improvements.
"""
        ttk.Label(lf3, text=about_text, foreground=COLORS['text_muted']).pack()
    
    def _create_results_tab(self, parent):
        """Create results display tab."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        
        # Summary counters
        cnt_frame = ttk.Frame(parent)
        cnt_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=16)
        
        self.sum_vars = {}
        for i, (key, label) in enumerate([
            ('packages', 'Packages'), ('new', 'New Packages'), 
            ('fp_added', 'Footprints Added'), ('fp_skipped', 'Footprints Skipped'),
            ('models', '3D Models'), ('sym_add', 'Symbols Added'),
            ('sym_ren', 'Symbols Renamed'), ('sym_ovr', 'Symbols Replaced'), ('errors', 'Errors')
        ]):
            self.sum_vars[key] = tk.StringVar(value="0")
            f = ttk.Frame(cnt_frame)
            f.pack(side="left", padx=12)
            ttk.Label(f, text=label, foreground=COLORS['text_muted']).pack()
            ttk.Label(f, textvariable=self.sum_vars[key], font=("Segoe UI", 14, "bold")).pack()
        
        # Results tree
        cols = ("package", "status", "symbols", "footprints", "models", "notes")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=15)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0,8))
        
        # Scrollbar
        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        vsb.grid(row=1, column=1, sticky="ns", pady=(0,8))
        self.tree.configure(yscrollcommand=vsb.set)
        
        # Configure columns
        self.tree.heading("package", text="Package", command=lambda: self.sort_tree("package"))
        self.tree.heading("status", text="Status", command=lambda: self.sort_tree("status"))
        self.tree.heading("symbols", text="Symbols", command=lambda: self.sort_tree("symbols"))
        self.tree.heading("footprints", text="Footprints", command=lambda: self.sort_tree("footprints"))
        self.tree.heading("models", text="3D Models", command=lambda: self.sort_tree("models"))
        self.tree.heading("notes", text="Notes", command=lambda: self.sort_tree("notes"))
        
        self.tree.column("package", width=300, anchor="w")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("symbols", width=100, anchor="center")
        self.tree.column("footprints", width=100, anchor="center")
        self.tree.column("models", width=100, anchor="center")
        self.tree.column("notes", width=200, anchor="w")
        
        # Verbose log
        log_lf = ttk.Labelframe(parent, text="Verbose Log", padding=8)
        log_lf.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=16, pady=(0,16))
        parent.grid_rowconfigure(2, weight=1)
        
        self.log = ScrolledText(log_lf, wrap="word", height=10, font=("Consolas", 9),
                               bg=COLORS['panel'], fg=COLORS['text'], insertbackground=COLORS['text'])
        self.log.pack(fill="both", expand=True)
        
        # Log tags for colors
        self.log.tag_configure("INFO", foreground=COLORS['text'])
        self.log.tag_configure("OK", foreground=COLORS['success'])
        self.log.tag_configure("WARN", foreground=COLORS['warning'])
        self.log.tag_configure("ERR", foreground=COLORS['error'])
        self.log.tag_configure("STEP", foreground=COLORS['accent'])
        self.log.tag_configure("NEW", foreground=COLORS['success'], font=("Consolas", 10, "bold"))
        self.log.tag_configure("SKIP", foreground=COLORS['text_muted'])
        self.log.tag_configure("ADD", foreground=COLORS['success'], font=("Consolas", 10, "bold"))
        
        # Context menu for tree
        self.tree_menu = tk.Menu(self, tearoff=0, bg=COLORS['panel'], fg=COLORS['text'])
        self.tree_menu.add_command(label="View Details", command=self.view_result_details)
        self.tree_menu.add_command(label="Copy Package Name", command=self.copy_package_name)
        self.tree.bind("<Button-3>", self.show_tree_context_menu)
    
    def _create_status_bar(self):
        """Create status bar at bottom."""
        status_frame = ttk.Frame(self, relief="flat", borderwidth=1)
        status_frame.pack(side="bottom", fill="x")
        
        self.status_text = tk.StringVar(value="⏳ Ready")
        ttk.Label(status_frame, textvariable=self.status_text, anchor="w", 
                 padding=(8,4)).pack(side="left", fill="x", expand=True)
        
        self.progress_text = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.progress_text, anchor="e",
                 foreground=COLORS['text_muted'], padding=(8,4)).pack(side="right")

    
    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def log_message(self, msg, tag="INFO"):
        """Add message to log with optional tag."""
        try:
            self.log.insert("end", msg + "\n", tag)
            self.log.see("end")
            self.log.update_idletasks()
        except:
            pass
    
    def smart_log(self, *args):
        """Smart logging that auto-detects tag based on message content."""
        msg = " ".join(str(x) for x in args)
        
        # Detect tag from message content
        if "Skip" in msg or "skip" in msg:
            tag = "SKIP"
        elif "Added" in msg or "Add" in msg or "Imported" in msg or "Paired" in msg or "Copied" in msg:
            tag = "NEW"
        elif "ERROR" in msg or "Error" in msg or "error" in msg:
            tag = "ERR"
        elif "WARN" in msg or "Warning" in msg or "Renamed" in msg:
            tag = "WARN"
        else:
            tag = "INFO"
        
        self.log_message(msg, tag)
    
    def set_status(self, msg, progress=""):
        """Update status bar."""
        self.status_text.set(msg)
        self.progress_text.set(progress)
        self.update_idletasks()
    
    def sort_tree(self, col):
        """Sort treeview by column."""
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children('')]
        items.sort()
        for index, (val, k) in enumerate(items):
            self.tree.move(k, '', index)
    
    def show_tree_context_menu(self, event):
        """Show context menu for tree."""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)
    
    def view_result_details(self):
        """Show details of selected result."""
        sel = self.tree.selection()
        if sel:
            values = self.tree.item(sel[0])['values']
            msg = f"Package: {values[0]}\nStatus: {values[1]}\nSymbols: {values[2]}\nFootprints: {values[3]}\n3D Models: {values[4]}\nNotes: {values[5]}"
            messagebox.showinfo("Result Details", msg)
    
    def copy_package_name(self):
        """Copy selected package name to clipboard."""
        sel = self.tree.selection()
        if sel:
            name = self.tree.item(sel[0])['values'][0]
            self.clipboard_clear()
            self.clipboard_append(name)
            self.set_status(f"Copied: {name}")
    
    def clear_results(self):
        """Clear results display."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.log.delete("1.0", "end")
        for var in self.sum_vars.values():
            var.set("0")
        self.results = []
        self.set_status("⏳ Ready")

    
    # ═══════════════════════════════════════════════════════════════════════════
    # FILE PICKER METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def pick_source(self):
        """Pick source directory for components."""
        init_dir = self.source.get() or str(Path.home())
        p = filedialog.askdirectory(title="Select Source Package Directory", initialdir=init_dir)
        if p:
            self.source.set(p)
            if p not in self.recent_sources:
                self.recent_sources.insert(0, p)
                self.recent_sources = self.recent_sources[:10]
    
    def detect_and_set_nickname(self, force=False):
        """Populate the Footprint Nickname field from the user's fp-lib-table.

        Honors the Auto-detect toggle unless force=True (the manual Detect button
        or enabling the checkbox). Does nothing if no library is selected."""
        if not force and not self.auto_nick.get():
            return
        pstr = self.personal.get()
        if not pstr:
            return
        try:
            detected = _detect_fp_nickname(Path(pstr).expanduser(), self.fp_choice.get())
        except Exception:
            detected = None
        if detected and detected != self.nick.get():
            self.nick.set(detected)
            try:
                self.log_message(f"[Nickname] Using footprint library nickname '{detected}'", "INFO")
            except Exception:
                pass

    def pick_personal(self):
        """Pick personal library directory."""
        init_dir = self.personal.get() or str(Path.home())
        p = filedialog.askdirectory(title="Select Personal Library Directory", initialdir=init_dir)
        if p:
            self.personal.set(p)
            if p not in self.recent_personal:
                self.recent_personal.insert(0, p)
                self.recent_personal = self.recent_personal[:10]
            self.refresh_personal_candidates()
            self.detect_and_set_nickname()
    
    def pick_models_root(self):
        """Pick 3D models root directory."""
        init_dir = self.models_root.get() or str(Path.home())
        p = filedialog.askdirectory(title="Select 3D Models Root", initialdir=init_dir)
        if p:
            self.models_root.set(p)
    
    def refresh_personal_candidates(self):
        """Update dropdown lists for symbol/footprint choices."""
        personal = Path(self.personal.get()).expanduser()
        if not personal.exists():
            return
        
        # Find symbol files
        sym_files = [f.name for f in personal.glob("*.kicad_sym")]
        if sym_files:
            self.sym_combo['values'] = sym_files
        
        # Find footprint directories
        fp_dirs = [d.name for d in personal.iterdir() if d.is_dir() and d.suffix == ".pretty"]
        if fp_dirs:
            self.fp_combo['values'] = fp_dirs
    
    def current_sym_path(self) -> Path:
        """Get current symbol file path."""
        return Path(self.personal.get()).expanduser() / self.sym_choice.get()
    
    def current_fp_dir(self) -> Path:
        """Get current footprint directory path."""
        return Path(self.personal.get()).expanduser() / self.fp_choice.get()
    
    def current_models_root(self) -> Path:
        """Get current 3D models root path."""
        mr = self.models_root.get()
        if mr:
            return Path(mr).expanduser()
        return Path(self.personal.get()).expanduser() / "3d"

    
    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN IMPORT OPERATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def run(self, inbox: bool = False):
        """Run the import process.

        inbox=True (from the Inbox tab): stay on the Inbox tab, skip the modal
        summary, and list the run's symbols inline for copy/paste."""
        source = Path(self.source.get()).expanduser()
        personal = Path(self.personal.get()).expanduser()
        
        if not source.exists():
            messagebox.showerror("Error", f"Source not found: {source}")
            return
        if not personal.exists():
            messagebox.showerror("Error", f"Personal library not found: {personal}")
            return
        
        # Clear results
        self.clear_results()
        
        # Setup
        sym_path = ensure_paths(personal, self.sym_choice.get(), self.fp_choice.get())
        
        # Open the per-library import log
        log_obj = ImportLog(personal)
        self._last_import_log = log_obj
        
        self.set_status("⚙️ Gathering packages...", "")
        self.log_message("=" * 70, "STEP")
        self.log_message("STARTING IMPORT", "STEP")
        self.log_message("=" * 70, "STEP")
        self.log_message(f"Source: {source}", "INFO")
        self.log_message(f"Personal: {personal}", "INFO")
        self.log_message(f"Dry run: {self.dry_run.get()}", "INFO")
        self.log_message(f"Import log: {log_obj.count()} package(s) on file", "INFO")
        
        # Gather packages
        all_pkgs = gather_packages(source, recurse=self.recurse.get())
        all_pkgs = prune_packages(all_pkgs, prefer=self.prefer_pkg.get())
        
        # Filter out already-imported packages (skip-list speed-up)
        skipped_already = []
        if self.skip_imported.get() and not self.overwrite.get():
            pkgs = []
            for kind, p in all_pkgs:
                if log_obj.contains(kind, p):
                    skipped_already.append((kind, p))
                else:
                    pkgs.append((kind, p))
        else:
            pkgs = list(all_pkgs)
        
        if skipped_already:
            self.log_message(
                f"\n⏩ Skipping {len(skipped_already)} package(s) already in import log "
                f"(uncheck 'Skip already-imported' to force re-import)",
                "SKIP",
            )
            # Show a few examples in the log
            for kind, p in skipped_already[:5]:
                self.log_message(f"   • {kind}: {p.name}", "SKIP")
            if len(skipped_already) > 5:
                self.log_message(f"   …and {len(skipped_already) - 5} more", "SKIP")
        
        self.log_message(f"\nFound {len(all_pkgs)} packages, processing {len(pkgs)} new", "OK")
        self.sum_vars['packages'].set(str(len(all_pkgs)))
        
        # Process each package
        results = []
        for i, (kind, path) in enumerate(pkgs, 1):
            self.set_status(f"⚙️ Processing {kind}: {path.name}", f"{i}/{len(pkgs)}")
            self.log_message(f"\n{'='*70}", "STEP")
            self.log_message(f"[{i}/{len(pkgs)}] {kind.upper()}: {path.name}", "STEP")
            self.log_message(f"{'='*70}", "STEP")
            
            try:
                res = process_one_package(
                    kind, path, personal, self.fp_choice.get(), sym_path,
                    fp_nick=self.nick.get(),
                    autoset_fp=self.autoset.get(),
                    overwrite=self.overwrite.get(),
                    mode=self.dedupe_mode.get(),
                    ignore_footprint_for_dedupe=self.ignore_fp_dedupe.get(),
                    duplicate_mode=self.dup_handling.get(),
                    dry_run=self.dry_run.get(),
                    log=self.smart_log,
                    prefer_model_ext="step",
                    force_relative_models=self.rel_models.get()
                )
                results.append(res)
                
                # Remember in the import log only when not a dry run AND no errors
                if not self.dry_run.get() and not res["errors"]:
                    log_obj.remember(kind, path, summary=res)
                
                # Update display
                status = "✅ OK" if not res['errors'] else "❌ ERR"
                self.tree.insert("", "end", values=(
                    path.name,
                    status,
                    f"{res['symbols_added']}+{res['symbols_renamed']}",
                    f"{res['footprints_imported']}+{res['footprints_skipped']}",
                    f"{res['models_copied']}+{res['models_paired']}",
                    res['errors'][0] if res['errors'] else ""
                ))
                
            except Exception as e:
                self.log_message(f"ERROR: {e}", "ERR")
                self.tree.insert("", "end", values=(path.name, "❌ ERR", "0", "0", "0", str(e)))
        
        # Persist import log (additive — never lose what was already there)
        if not self.dry_run.get():
            log_obj.save()
        
        # Update summary
        total = {'fp_add': 0, 'fp_skip': 0, 'models': 0, 'sym_add': 0, 'sym_ren': 0,
                 'sym_ovr': 0, 'err': 0, 'new': 0, 'paired': 0}
        for r in results:
            total['fp_add'] += r['footprints_imported']
            total['fp_skip'] += r['footprints_skipped']
            total['models'] += r['models_copied']
            total['sym_add'] += r['symbols_added']
            total['sym_ren'] += r['symbols_renamed']
            total['sym_ovr'] += r['symbols_overwritten']
            total['paired'] += r['models_paired']
            total['err'] += len(r['errors'])
            if r['symbols_added'] > 0 or r['footprints_imported'] > 0:
                total['new'] += 1
        # Count of packages that were entirely skipped via the log
        total['skipped_from_log'] = len(skipped_already)
        
        self.sum_vars['new'].set(str(total['new']))
        self.sum_vars['fp_added'].set(str(total['fp_add']))
        self.sum_vars['fp_skipped'].set(str(total['fp_skip']))
        self.sum_vars['models'].set(str(total['models']))
        self.sum_vars['sym_add'].set(str(total['sym_add']))
        self.sum_vars['sym_ren'].set(str(total['sym_ren']))
        self.sum_vars['sym_ovr'].set(str(total['sym_ovr']))
        self.sum_vars['errors'].set(str(total['err']))
        
        # Log comprehensive summary
        self.log_message("\n" + "=" * 70, "STEP")
        self.log_message("IMPORT SUMMARY", "STEP")
        self.log_message("=" * 70, "STEP")
        self.log_message(f"\n📦 PACKAGES:", "INFO")
        self.log_message(f"   • Found: {len(all_pkgs)} total", "INFO")
        if skipped_already:
            self.log_message(f"   • Already imported: {len(skipped_already)} skipped via log", "SKIP")
        self.log_message(f"   • Processed: {len(pkgs)}", "INFO")
        self.log_message(f"   • New content: {total['new']} packages had new content", "NEW" if total['new'] > 0 else "SKIP")
        self.log_message(f"   • No new content: {len(pkgs) - total['new']} packages", "SKIP")
        
        self.log_message(f"\n📚 SYMBOLS:", "INFO")
        self.log_message(f"   • Added: {total['sym_add']} new symbols", "NEW" if total['sym_add'] > 0 else "SKIP")
        if total['sym_ren'] > 0:
            self.log_message(f"   • Renamed: {total['sym_ren']} (added with _imp suffix)", "WARN")
        if total['sym_ovr'] > 0:
            self.log_message(f"   • Replaced: {total['sym_ovr']} (overwrote existing)", "WARN")
        
        self.log_message(f"\n👣 FOOTPRINTS:", "INFO")
        self.log_message(f"   • Added: {total['fp_add']} new footprints", "NEW" if total['fp_add'] > 0 else "SKIP")
        self.log_message(f"   • Skipped: {total['fp_skip']} (already existed)", "SKIP")
        
        self.log_message(f"\n🎨 3D MODELS:", "INFO")
        self.log_message(f"   • Copied: {total['models']} new models", "NEW" if total['models'] > 0 else "SKIP")
        self.log_message(f"   • Paired: {total['paired']} into footprints", "NEW" if total['paired'] > 0 else "SKIP")
        
        if total['err'] > 0:
            self.log_message(f"\n❌ ERRORS: {total['err']}", "ERR")
        
        # Final status
        if total['new'] == 0 and not skipped_already:
            self.log_message("\n✅ All packages up to date - nothing new to import", "OK")
            self.set_status(f"✅ Complete - {len(all_pkgs)} packages checked, all up to date", "")
        elif total['new'] == 0 and skipped_already:
            self.log_message(f"\n✅ All {len(all_pkgs)} packages already imported (used skip log)", "OK")
            self.set_status(f"✅ Complete - {len(skipped_already)} skipped via log, nothing new", "")
        else:
            self.log_message(f"\n✅ IMPORT COMPLETE!", "OK")
            self.log_message(f"   Added {total['sym_add']} symbols + {total['fp_add']} footprints + {total['models']} 3D models", "OK")
            self.set_status(f"✅ Complete - Added content from {total['new']}/{len(pkgs)} packages", "")
        
        self.results = results
        
        # Save prefs
        self.save_prefs()
        
        # Done
        if not total['err']:
            msg = f"✅ Complete! Processed {len(pkgs)} new packages ({len(skipped_already)} skipped via log)"
        else:
            msg = f"⚠️ Complete with {total['err']} errors"
        self.set_status(msg, "")
        self.log_message(f"\n{'='*70}", "STEP")
        self.log_message(msg, "OK" if not total['err'] else "WARN")
        self.log_message(f"{'='*70}", "STEP")
        
        # Kept so the Inbox tab's "Full Summary…" can reopen the dialog
        self._last_run_args = (results, total, all_pkgs, skipped_already, log_obj)
        if inbox:
            self._inbox_show_results(results, total, all_pkgs, skipped_already)
            return

        # Switch to the Results tab (look it up by label so adding new tabs
        # in future doesn't silently send the user to the wrong place)
        self._switch_to_tab("Results")
        
        # Detailed summary popup (lists actual part names, not just counts)
        self._show_import_summary_dialog(results, total, all_pkgs, skipped_already, log_obj)

    # ─── Helpers used by run() ───────────────────────────────────────────
    def _switch_to_tab(self, label_substr: str):
        """Switch the main notebook to whichever tab has ``label_substr``
        in its label. Falls back to the last tab if not found."""
        try:
            n = self.notebook.index("end")
            for i in range(n):
                txt = self.notebook.tab(i, "text") or ""
                if label_substr.lower() in txt.lower():
                    self.notebook.select(i)
                    return
            self.notebook.select(n - 1)
        except Exception:
            pass
    
    def _show_import_summary_dialog(self, results, total, all_pkgs,
                                    skipped_already, log_obj):
        """Modal that lists every actual part imported, not just totals.
        
        Sections (only shown when non-empty):
          • New Symbols     ─ exact symbol names added
          • Renamed Symbols ─ "old → new"
          • Overwritten     ─ symbol names that replaced existing
          • New Footprints  ─ filenames
          • 3D Models Copied
          • Models Paired   ─ "footprint → model"
          • Errors
          • Skipped via log ─ packages skipped because they were already imported
        """
        win = tk.Toplevel(self)
        win.title("Import Complete")
        win.geometry("780x620")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        win.grab_set()
        
        # Header
        head = ttk.Frame(win)
        head.pack(fill="x", padx=16, pady=12)
        if total.get('err'):
            ttk.Label(head, text=f"⚠ Import complete with {total['err']} error(s)",
                      font=("Segoe UI", 14, "bold"),
                      foreground=COLORS['warning']).pack(anchor="w")
        else:
            ttk.Label(head, text="✅ Import complete",
                      font=("Segoe UI", 14, "bold"),
                      foreground=COLORS['success']).pack(anchor="w")
        
        head_lines = [
            f"Packages found in source: {len(all_pkgs)}",
            f"Skipped via import log:  {len(skipped_already)}",
            f"Processed this run:      {len(results)}",
            f"Net new (added content): {total.get('new', 0)}",
        ]
        ttk.Label(head, text="\n".join(head_lines),
                  foreground=COLORS['text_muted'],
                  font=("Consolas", 10)).pack(anchor="w", pady=(6, 0))
        
        # Body — scrollable text with all the names
        body = ScrolledText(win, wrap="word", bg=COLORS['panel'],
                            fg=COLORS['text'], font=("Consolas", 10))
        body.pack(fill="both", expand=True, padx=16, pady=8)
        
        body.tag_configure("h", font=("Segoe UI", 11, "bold"),
                           foreground=COLORS['accent'])
        body.tag_configure("muted", foreground=COLORS['text_muted'])
        body.tag_configure("ok", foreground=COLORS['success'])
        body.tag_configure("warn", foreground=COLORS['warning'])
        body.tag_configure("err", foreground=COLORS['error'])
        
        # Aggregate name lists across all packages
        added_syms = []
        renamed_syms = []
        overwritten_syms = []
        added_fps = []
        copied_models = []
        paired_pairs = []
        all_errors = []
        for r in results:
            added_syms.extend(r.get("symbols_added_names", []))
            renamed_syms.extend(r.get("symbols_renamed_pairs", []))
            overwritten_syms.extend(r.get("symbols_overwritten_names", []))
            added_fps.extend(r.get("footprints_added_names", []))
            copied_models.extend(r.get("models_copied_names", []))
            paired_pairs.extend(r.get("models_paired_pairs", []))
            for e in r.get("errors", []):
                all_errors.append((Path(r.get("package", "")).name, e))
        
        def section(title, items, fmt=None, tag="ok"):
            if not items:
                return
            body.insert("end", f"\n{title} ({len(items)})\n", "h")
            for it in items:
                line = fmt(it) if fmt else str(it)
                body.insert("end", f"   • {line}\n", tag)
        
        # Sort within each list, dedup while preserving first occurrence
        def _uniq(seq):
            seen = set(); out = []
            for x in seq:
                key = x if isinstance(x, str) else tuple(x)
                if key not in seen:
                    seen.add(key); out.append(x)
            return out
        
        added_syms_u = sorted(_uniq(added_syms), key=str.lower)
        renamed_u = sorted(_uniq(renamed_syms), key=lambda p: p[0].lower())
        overwritten_u = sorted(_uniq(overwritten_syms), key=str.lower)
        added_fps_u = sorted(_uniq(added_fps), key=str.lower)
        copied_models_u = sorted(_uniq(copied_models), key=str.lower)
        paired_u = sorted(_uniq(paired_pairs), key=lambda p: p[0].lower())
        
        section("📚 New Symbols", added_syms_u, tag="ok")
        section("📚 Renamed Symbols", renamed_u, fmt=lambda p: f"{p[0]}  →  {p[1]}", tag="warn")
        section("📚 Overwritten Symbols", overwritten_u, tag="warn")
        section("👣 New Footprints", added_fps_u, tag="ok")
        section("🎲 3D Models Copied", copied_models_u, tag="ok")
        section("🎲 Models Paired into Footprints", paired_u,
                fmt=lambda p: f"{p[0]}  →  {p[1]}", tag="ok")
        section("❌ Errors", all_errors, fmt=lambda p: f"[{p[0]}]  {p[1]}", tag="err")
        
        if skipped_already:
            body.insert("end", f"\n⏩ Skipped via Import Log ({len(skipped_already)})\n", "h")
            for kind, p in skipped_already[:50]:
                body.insert("end", f"   • {kind}: {p.name}\n", "muted")
            if len(skipped_already) > 50:
                body.insert("end", f"   …and {len(skipped_already) - 50} more\n", "muted")
        
        # If everything was empty, say so explicitly
        if not any([added_syms_u, renamed_u, overwritten_u, added_fps_u,
                    copied_models_u, paired_u, all_errors]):
            body.insert("end",
                        "\n(Nothing new was added.)\n"
                        "All processed packages either contained content "
                        "that already exists in the library, or were skipped "
                        "via the import log.\n", "muted")
        
        body.configure(state="disabled")
        body.see("1.0")
        
        # Footer
        footer = ttk.Frame(win)
        footer.pack(fill="x", padx=16, pady=(0, 14))
        log_msg = ""
        if log_obj is not None:
            log_msg = f"📜 Import log now contains {log_obj.count()} package(s)."
        ttk.Label(footer, text=log_msg, foreground=COLORS['text_muted']
                  ).pack(side="left")
        ttk.Button(footer, text="Close", command=win.destroy,
                   style="Accent.TButton"
                   ).pack(side="right", ipadx=14, ipady=4)
    
    def show_import_log_gui(self):
        """View / search / clear the per-library import log."""
        personal = Path(self.personal.get()).expanduser() if self.personal.get() else None
        if not personal or not personal.exists():
            messagebox.showerror(
                "No library selected",
                "Pick a personal library directory first — the import log "
                "is stored inside it.",
            )
            return
        
        log_obj = ImportLog(personal)
        
        win = tk.Toplevel(self)
        win.title(f"Import Log — {personal.name}")
        win.geometry("950x600")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        win.grab_set()
        
        # Header
        head = ttk.Frame(win)
        head.pack(fill="x", padx=12, pady=12)
        ttk.Label(head, text=f"Import log: {log_obj.count()} package(s)",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(
            head,
            text=("Each entry remembers the path, size, and modification time "
                  "of a package that was imported. Re-running an import will "
                  "skip these. Removing an entry causes that package to be "
                  "re-imported next time. Replacing the file on disk (different "
                  "size or timestamp) also triggers re-import automatically."),
            foreground=COLORS['text_muted'], wraplength=900, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(head, text=f"File: {log_obj.path}",
                  foreground=COLORS['text_muted'],
                  font=("Consolas", 9)).pack(anchor="w", pady=(4, 0))
        
        # Search row
        srow = ttk.Frame(win)
        srow.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(srow, text="🔍 Filter:").pack(side="left", padx=(0, 8))
        sv = tk.StringVar()
        ttk.Entry(srow, textvariable=sv).pack(side="left", fill="x", expand=True)
        
        # Tree
        tree_frame = ttk.Frame(win)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=8)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        cols = ("kind", "name", "imported_at", "summary", "path")
        ltree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                             selectmode="extended")
        ltree.grid(row=0, column=0, sticky="nsew")
        lvsb = ttk.Scrollbar(tree_frame, orient="vertical", command=ltree.yview)
        lvsb.grid(row=0, column=1, sticky="ns")
        ltree.configure(yscrollcommand=lvsb.set)
        
        ltree.heading("kind", text="Kind")
        ltree.heading("name", text="Name")
        ltree.heading("imported_at", text="Imported")
        ltree.heading("summary", text="Summary")
        ltree.heading("path", text="Path")
        ltree.column("kind", width=60, anchor="center", stretch=False)
        ltree.column("name", width=200, anchor="w")
        ltree.column("imported_at", width=140, anchor="w")
        ltree.column("summary", width=240, anchor="w")
        ltree.column("path", width=300, anchor="w")
        
        # Cache: tree_item_id -> (kind, full path)
        item_meta = {}
        
        def populate(filter_text=""):
            for it in ltree.get_children():
                ltree.delete(it)
            item_meta.clear()
            ft = filter_text.lower()
            for fp_key, entry in sorted(
                log_obj.list_entries(),
                key=lambda kv: kv[1].get("imported_at", ""),
                reverse=True,
            ):
                p = Path(entry.get("path", ""))
                if ft and ft not in p.name.lower() and ft not in entry.get("path", "").lower():
                    continue
                summary = entry.get("summary", {}) or {}
                summary_text = (
                    f"{summary.get('symbols_added',0)}sym "
                    f"+ {summary.get('footprints_imported',0)}fp "
                    f"+ {summary.get('models_copied',0)}3d"
                )
                iid = ltree.insert("", "end", values=(
                    entry.get("kind", ""),
                    p.name,
                    entry.get("imported_at", ""),
                    summary_text,
                    str(p),
                ))
                item_meta[iid] = (entry.get("kind", ""), p)
        
        populate()
        sv.trace_add("write", lambda *a: populate(sv.get()))
        
        # Buttons
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=12)
        
        def remove_selected():
            sel = ltree.selection()
            if not sel:
                messagebox.showinfo(
                    "No selection",
                    "Click rows in the table to select them, then "
                    "'Remove Selected'.", parent=win)
                return
            if not messagebox.askyesno(
                "Confirm",
                f"Remove {len(sel)} entr{'ies' if len(sel) != 1 else 'y'} "
                "from the import log?\n\nThose packages will be re-imported "
                "next time you run the import.",
                parent=win,
            ):
                return
            for iid in sel:
                kind, p = item_meta.get(iid, ("", Path("")))
                if kind:
                    log_obj.forget(kind, p)
            log_obj.save()
            populate(sv.get())
            head.winfo_children()[0].config(text=f"Import log: {log_obj.count()} package(s)")
        
        def reset_all():
            if not messagebox.askyesno(
                "Reset Import Log",
                f"Clear the entire import log ({log_obj.count()} package(s))?\n\n"
                "Next import will re-process EVERY package found in the source "
                "directory. This is the right move if you suspect packages have "
                "changed without their timestamps updating, or if you want to "
                "force-rebuild the library.",
                parent=win,
            ):
                return
            log_obj.reset()
            log_obj.save()
            populate(sv.get())
            head.winfo_children()[0].config(text="Import log: 0 package(s)")
        
        ttk.Button(btns, text="🗑️ Remove Selected", command=remove_selected
                   ).pack(side="left", padx=4)
        ttk.Button(btns, text="🔥 Reset Entire Log…", command=reset_all
                   ).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=win.destroy,
                   style="Accent.TButton"
                   ).pack(side="right", ipadx=14, ipady=4)

    
    # ═══════════════════════════════════════════════════════════════════════════
    # TOOL METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def export_report(self):
        """Export results to CSV."""
        if not self.results:
            messagebox.showwarning("No Data", "No results to export. Run an import first.")
            return
        
        fname = filedialog.asksaveasfilename(
            title="Export Report",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if fname:
            try:
                with open(fname, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Package", "Kind", "FP+", "FP-", "3D", "SYM+", "REN", "OVR", "Errors"])
                    for r in self.results:
                        writer.writerow([
                            Path(r['package']).name, r['kind'],
                            r['footprints_imported'], r['footprints_skipped'],
                            r['models_copied'], r['symbols_added'],
                            r['symbols_renamed'], r['symbols_overwritten'],
                            "; ".join(r['errors'])
                        ])
                messagebox.showinfo("Exported", f"Report saved to:\n{fname}")
            except Exception as e:
                messagebox.showerror("Export Failed", str(e))
    
    def view_library_parts(self):
        """View all parts in library."""
        sym_path = self.current_sym_path()
        if not sym_path.exists():
            messagebox.showerror("Error", f"Symbol file not found: {sym_path}")
            return
        
        syms = list_symbols(sym_path)
        
        win = tk.Toplevel(self)
        win.title(f"Library Parts - {sym_path.name}")
        win.geometry("800x600")
        win.configure(bg=COLORS['bg'])
        
        # Search
        search_frame = ttk.Frame(win)
        search_frame.pack(fill="x", padx=8, pady=8)
        ttk.Label(search_frame, text="Search:").pack(side="left", padx=4)
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var, width=40)
        search_entry.pack(side="left", padx=4, fill="x", expand=True)
        
        # Listbox
        listbox = tk.Listbox(win, bg=COLORS['panel'], fg=COLORS['text'], 
                            selectbackground=COLORS['accent'], font=("Segoe UI", 10))
        listbox.pack(fill="both", expand=True, padx=8, pady=8)
        
        # Scrollbar
        scroll = ttk.Scrollbar(win, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scroll.set)
        
        def update_list(*args):
            listbox.delete(0, "end")
            query = search_var.get().lower()
            for name, block in sorted(syms, key=lambda x: x[0].lower()):
                if query in name.lower():
                    listbox.insert("end", name)
        
        search_var.trace_add("write", update_list)
        update_list()
        
        ttk.Label(win, text=f"Total: {len(syms)} symbols", foreground=COLORS['text_muted']).pack(pady=4)
    
    def refresh_library_browser(self):
        """Load/reload all parts in the library browser."""
        sym_path = self.current_sym_path()
        if not sym_path.exists():
            self.lib_info_var.set(f"⚠️ Symbol file not found: {sym_path.name}")
            return
        
        self.lib_info_var.set(f"📖 Loading {sym_path.name}...")
        self.update_idletasks()
        
        # Clear tree
        for item in self.lib_tree.get_children():
            self.lib_tree.delete(item)
        
        # Load symbols
        try:
            syms = list_symbols(sym_path)
            fp_dir = self.current_fp_dir()
            
            # Store for filtering
            self.lib_data = []
            
            for name, block in syms:
                # Extract footprint property
                footprint = ""
                fp_match = re.search(r'\(property\s+"Footprint"\s+"([^"]*)"', block)
                if fp_match:
                    footprint = fp_match.group(1).split(":")[-1] if ":" in fp_match.group(1) else fp_match.group(1)
                
                # Extract MPN (manufacturer part number) - check various field names
                mpn = ""
                mpn_patterns = [
                    r'\(property\s+"MPN"\s+"([^"]*)"',
                    r'\(property\s+"Manufacturer_Part_Number"\s+"([^"]*)"',
                    r'\(property\s+"Mfr Part Number"\s+"([^"]*)"',
                    r'\(property\s+"Part Number"\s+"([^"]*)"',
                    r'\(property\s+"PartNumber"\s+"([^"]*)"',
                    r'\(property\s+"P/N"\s+"([^"]*)"',
                    r'\(property\s+"Mfr_PN"\s+"([^"]*)"',
                    r'\(property\s+"ManufacturerPartNumber"\s+"([^"]*)"',
                ]
                for pattern in mpn_patterns:
                    mpn_match = re.search(pattern, block, re.IGNORECASE)
                    if mpn_match:
                        mpn = mpn_match.group(1)
                        break
                
                # FALLBACK: If no MPN field found, use symbol name
                if not mpn:
                    mpn = name  # Symbol name is often the part number
                
                # Count pins
                pin_count = len(re.findall(r'\(pin\s+', block))
                
                # Try to find 3D model from footprint
                model_3d = ""
                if footprint and fp_dir.exists():
                    fp_file = fp_dir / f"{footprint}.kicad_mod"
                    if fp_file.exists():
                        try:
                            fp_text = fp_file.read_text(encoding="utf-8", errors="ignore")
                            model_match = re.search(r'\(model\s+"([^"]+)"', fp_text)
                            if model_match:
                                model_path = model_match.group(1)
                                # Extract just filename
                                model_3d = Path(model_path).name
                        except:
                            pass
                
                self.lib_data.append({
                    'symbol': name,
                    'footprint': footprint or "(none)",
                    'mpn': mpn or "(none)",
                    'model_3d': model_3d or "(none)",
                    'pins': str(pin_count) if pin_count > 0 else "0"
                })
            
            # Display all
            self.filter_library_browser()
            self.lib_info_var.set(f"✅ Loaded {len(syms)} symbols from {sym_path.name}")
            self.lib_count_label.config(text=f"Total: {len(syms)}")
            
        except Exception as e:
            self.lib_info_var.set(f"❌ Error: {e}")
    
    def filter_library_browser(self):
        """Filter library browser based on search text."""
        if not hasattr(self, 'lib_data'):
            return
        
        # Clear tree
        for item in self.lib_tree.get_children():
            self.lib_tree.delete(item)
        
        # Filter
        query = self.lib_search_var.get().lower()
        count = 0
        
        for data in self.lib_data:
            if not query or query in data['symbol'].lower() or query in data['footprint'].lower() or query in data['mpn'].lower():
                self.lib_tree.insert("", "end", values=(
                    data['symbol'],
                    data['footprint'],
                    data['mpn'],
                    data['model_3d'],
                    data['pins']
                ))
                count += 1
        
        if query:
            self.lib_count_label.config(text=f"Showing: {count}/{len(self.lib_data)}")
        else:
            self.lib_count_label.config(text=f"Total: {len(self.lib_data)}")
    
    def sort_lib_tree(self, col):
        """Sort library tree by column."""
        items = [(self.lib_tree.set(k, col), k) for k in self.lib_tree.get_children('')]
        items.sort()
        for index, (val, k) in enumerate(items):
            self.lib_tree.move(k, '', index)
    
    def audit_footprints_gui(self):
        """Audit footprints for 3D models."""
        fp_dir = self.current_fp_dir()
        if not fp_dir.exists():
            messagebox.showerror("Error", f"Footprint directory not found: {fp_dir}")
            return
        
        win = tk.Toplevel(self)
        win.title("Audit Footprints (3D Models)")
        win.geometry("900x600")
        win.configure(bg=COLORS['bg'])
        
        ttk.Label(win, text=f"Scanning: {fp_dir}").pack(padx=8, pady=8)
        
        # Tree
        cols = ("footprint", "status", "count")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=20)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        
        tree.heading("footprint", text="Footprint")
        tree.heading("status", text="Status")
        tree.heading("count", text="Models")
        
        tree.column("footprint", width=500)
        tree.column("status", width=150)
        tree.column("count", width=100, anchor="center")
        
        # Scan
        mods = list(fp_dir.glob("*.kicad_mod"))
        for mod in sorted(mods):
            txt = mod.read_text(encoding="utf-8", errors="ignore")
            model_matches = re.findall(r'\(model\s+', txt)
            count = len(model_matches)
            status = "✅ Has models" if count > 0 else "⚠️ No models"
            icon = "✅" if count > 0 else "⚠️"
            tree.insert("", "end", values=(mod.stem, status, count), tags=(icon,))
        
        tree.tag_configure("✅", foreground=COLORS['success'])
        tree.tag_configure("⚠️", foreground=COLORS['warning'])
        
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)
    
    def fix_3d_paths_gui(self):
        """Fix 3D model paths in footprints - Enhanced with manual selection."""
        fp_dir = self.current_fp_dir()
        models_root = self.current_models_root()
        sym_path = self.current_sym_path()
        
        if not fp_dir.exists():
            messagebox.showerror("Error", f"Footprint directory not found: {fp_dir}")
            return
        
        win = tk.Toplevel(self)
        win.title("Fix 3D Paths - Select Footprints")
        win.geometry("1200x750")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        
        # Track manual selections
        manual_selections = {}  # footprint_name -> model_path
        
        # Options
        opt_frame = ttk.Labelframe(win, text="Options", padding=12)
        opt_frame.pack(fill="x", padx=12, pady=(12,8))
        
        add_missing = tk.BooleanVar(value=True)
        rel = tk.BooleanVar(value=self.rel_models.get())
        
        ttk.Checkbutton(opt_frame, text="Add models to footprints with none", variable=add_missing).pack(anchor="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Use relative paths", variable=rel).pack(anchor="w", pady=2)
        
        # Add instruction banner BEFORE the list (more visible)
        instruction_frame = ttk.Frame(win)
        instruction_frame.pack(fill="x", padx=12, pady=8)
        
        instruction_text = (
            "📋 WORKFLOW: "
            "① Click 'SCAN' below → "
            "② Check boxes for items to update → "
            "③ Double-click ❓ rows to manually pick 3D models → "
            "④ Click big 'APPLY' button at bottom"
        )
        ttk.Label(instruction_frame, text=instruction_text, 
                 foreground=COLORS['accent'], font=("Segoe UI", 10, "bold"),
                 background=COLORS['panel'], padding=8, relief="solid", borderwidth=1).pack(fill="x")
        
        # Footprint list with checkboxes
        list_frame = ttk.Labelframe(win, text="Footprints (Check boxes = will update | Double-click = manual 3D model select)", padding=12)
        list_frame.pack(fill="both", expand=True, padx=12, pady=8)
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)
        
        # Tree view
        cols = ("footprint", "current", "new_model", "action", "source")
        tree = ttk.Treeview(list_frame, columns=cols, show="tree headings", selectmode="extended")
        tree.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbars
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=vsb.set)
        
        hsb = ttk.Scrollbar(list_frame, orient="horizontal", command=tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        tree.configure(xscrollcommand=hsb.set)
        
        # Configure columns
        tree.heading("#0", text="☐ Select")
        tree.heading("footprint", text="Footprint")
        tree.heading("current", text="Current 3D Model")
        tree.heading("new_model", text="New 3D Model")
        tree.heading("action", text="Action")
        tree.heading("source", text="Source")
        
        tree.column("#0", width=80, anchor="center")
        tree.column("footprint", width=200, anchor="w")
        tree.column("current", width=220, anchor="w")
        tree.column("new_model", width=220, anchor="w")
        tree.column("action", width=100, anchor="center")
        tree.column("source", width=120, anchor="center")
        
        # Track checked items
        checked_items = {}
        
        def toggle_check(event):
            # Only toggle if clicking on checkbox column
            region = tree.identify_region(event.x, event.y)
            if region == "tree":
                item = tree.identify_row(event.y)
                if item:
                    current = tree.item(item, "text")
                    if current == "☐":
                        tree.item(item, text="☑")
                        checked_items[item] = True
                    else:
                        tree.item(item, text="☐")
                        checked_items[item] = False
        
        def manual_select_model(event):
            """Let user manually select a 3D model for the footprint."""
            item = tree.identify_row(event.y)
            if not item:
                return
            
            values = tree.item(item, "values")
            fp_name = values[0]
            
            # Show dialog to pick 3D model
            select_win = tk.Toplevel(win)
            select_win.title(f"Select 3D Model for {fp_name}")
            select_win.geometry("800x600")
            select_win.configure(bg=COLORS['bg'])
            select_win.transient(win)
            select_win.grab_set()
            
            ttk.Label(select_win, text=f"Select 3D Model for: {fp_name}",
                     font=("Segoe UI", 12, "bold")).pack(padx=12, pady=(12,4))
            
            # Add instruction
            instr = ttk.Label(select_win, 
                             text="① Pick model from list below OR click 'Browse' → ② Click 'Use Selected' button at bottom",
                             foreground=COLORS['accent'], font=("Segoe UI", 9, "bold"))
            instr.pack(padx=12, pady=(0,12))
            
            # List available models
            model_frame = ttk.Labelframe(select_win, text="Available 3D Models (double-click or select + click button)", padding=12)
            model_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
            
            # Model tree
            model_cols = ("name", "type", "path")
            model_tree = ttk.Treeview(model_frame, columns=model_cols, show="headings")
            model_tree.pack(side="left", fill="both", expand=True)
            
            model_vsb = ttk.Scrollbar(model_frame, orient="vertical", command=model_tree.yview)
            model_vsb.pack(side="right", fill="y")
            model_tree.configure(yscrollcommand=model_vsb.set)
            
            model_tree.heading("name", text="Model Name")
            model_tree.heading("type", text="Type")
            model_tree.heading("path", text="Path")
            
            model_tree.column("name", width=250, anchor="w")
            model_tree.column("type", width=80, anchor="center")
            model_tree.column("path", width=400, anchor="w")
            
            # Search
            search_frame = ttk.Frame(select_win)
            search_frame.pack(fill="x", padx=12, pady=(0,12))
            
            ttk.Label(search_frame, text="🔍 Filter:").pack(side="left", padx=(0,8))
            search_var = tk.StringVar()
            search_entry = ttk.Entry(search_frame, textvariable=search_var, width=40)
            search_entry.pack(side="left", fill="x", expand=True)
            
            # Populate models
            def populate_models(filter_text=""):
                for item in model_tree.get_children():
                    model_tree.delete(item)
                
                filter_lower = filter_text.lower()
                
                # Find all 3D models
                if models_root.exists():
                    for ext in [".step", ".stp", ".wrl"]:
                        for model in models_root.rglob(f"*{ext}"):
                            if filter_text and filter_lower not in model.stem.lower():
                                continue
                            
                            model_type = "STEP" if ext in [".step", ".stp"] else "WRL"
                            rel_path = model.relative_to(models_root)
                            
                            model_tree.insert("", "end", values=(
                                model.stem,
                                model_type,
                                str(rel_path)
                            ), tags=(str(model),))
            
            def on_search(*args):
                populate_models(search_var.get())
            
            search_var.trace_add("write", on_search)
            
            # Buttons
            btn_frame = ttk.Frame(select_win)
            btn_frame.pack(fill="x", padx=12, pady=(0,12))
            
            def select_from_tree():
                """Use selected model from tree."""
                sel = model_tree.selection()
                if not sel:
                    messagebox.showwarning("No Selection", "Please select a 3D model from the list")
                    return
                
                model_path = Path(model_tree.item(sel[0], "tags")[0])
                manual_selections[fp_name] = model_path
                
                # Update main tree
                tree.item(item, values=(
                    fp_name,
                    values[1],  # current
                    model_path.name,
                    "🎯 Manual",
                    "Manual"
                ), tags=("manual",))
                
                # Auto-check
                tree.item(item, text="☑")
                checked_items[item] = True
                
                select_win.destroy()
                
                # Update status to remind user
                status_var.set(f"✅ Manual model selected for {fp_name} - Click 'APPLY SELECTED CHANGES' when ready")
            
            def browse_file():
                """Browse for any 3D file."""
                file_path = filedialog.askopenfilename(
                    title="Select 3D Model",
                    filetypes=[
                        ("3D Models", "*.step *.stp *.wrl"),
                        ("STEP Files", "*.step *.stp"),
                        ("VRML Files", "*.wrl"),
                        ("All Files", "*.*")
                    ],
                    initialdir=models_root if models_root.exists() else None
                )
                
                if file_path:
                    model_path = Path(file_path)
                    manual_selections[fp_name] = model_path
                    
                    # Update main tree
                    tree.item(item, values=(
                        fp_name,
                        values[1],
                        model_path.name,
                        "🎯 Manual",
                        "Manual"
                    ), tags=("manual",))
                    
                    # Auto-check
                    tree.item(item, text="☑")
                    checked_items[item] = True
                    
                    select_win.destroy()
                    
                    # Update status to remind user
                    status_var.set(f"✅ Manual model selected for {fp_name} - Click 'APPLY SELECTED CHANGES' when ready")
            
            def clear_selection():
                """Clear manual selection."""
                if fp_name in manual_selections:
                    del manual_selections[fp_name]
                select_win.destroy()
                scan_footprints()  # Re-scan to show auto-match
            
            ttk.Button(btn_frame, text="📂 Browse for File...", 
                      command=browse_file).pack(side="left", padx=4)
            ttk.Button(btn_frame, text="✅ USE THIS MODEL (returns to main window)", 
                      command=select_from_tree,
                      style="Accent.TButton").pack(side="right", padx=4)
            ttk.Button(btn_frame, text="🔄 Clear Selection", 
                      command=clear_selection).pack(side="right", padx=4)
            ttk.Button(btn_frame, text="Cancel", 
                      command=select_win.destroy).pack(side="right", padx=4)
            
            # Initial populate
            populate_models()
            
            # Add double-click to model tree for quick selection
            def on_model_double_click(event):
                select_from_tree()
            
            model_tree.bind("<Double-1>", on_model_double_click)
            
            # Highlight similar names
            for item in model_tree.get_children():
                name = model_tree.item(item, "values")[0]
                if fp_name.lower() in name.lower() or name.lower() in fp_name.lower():
                    model_tree.selection_add(item)
                    model_tree.see(item)
                    break
        
        tree.bind("<Button-1>", toggle_check)
        tree.bind("<Double-1>", manual_select_model)
        
        # Scan button
        def scan_footprints():
            # Clear tree
            for item in tree.get_children():
                tree.delete(item)
            checked_items.clear()
            
            # Scan footprints
            all_models_idx = _available_models_index(models_root)
            
            for mod_file in sorted(fp_dir.glob("*.kicad_mod")):
                txt = mod_file.read_text(encoding="utf-8", errors="ignore")
                fp_name = mod_file.stem
                
                # Get current model
                model_match = re.search(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)
                current_model = ""
                if model_match:
                    current_model = model_match.group(1) or model_match.group(2)
                    if "/" in current_model or "\\" in current_model:
                        current_model = Path(current_model).name
                
                # Check for manual selection first
                if fp_name in manual_selections:
                    new_model = manual_selections[fp_name].name
                    action = "🎯 Manual"
                    tag = "manual"
                    source = "Manual"
                else:
                    # Find best model
                    best = _choose_local_model_smart(all_models_idx, fp_name, "step")
                    new_model = best.name if best else ""
                    source = "Auto-match"
                    
                    # Determine action
                    if not model_match and best and add_missing.get():
                        action = "➕ Add"
                        tag = "add"
                    elif model_match and best and current_model != new_model:
                        action = "🔧 Fix Path"
                        tag = "fix"
                    elif model_match and best and current_model == new_model:
                        action = "✓ OK"
                        tag = "ok"
                    elif not model_match and not best:
                        action = "❓ No Model"
                        tag = "missing"
                    else:
                        action = "—"
                        tag = ""
                
                # Add to tree
                item_id = tree.insert("", "end", 
                    text="☐",
                    values=(fp_name, current_model or "(none)", new_model or "(not found)", action, source),
                    tags=(tag,)
                )
                
                # Auto-check items that need fixing
                if tag in ("add", "fix", "manual"):
                    tree.item(item_id, text="☑")
                    checked_items[item_id] = True
            
            # Configure tags
            tree.tag_configure("add", foreground=COLORS['success'])
            tree.tag_configure("fix", foreground=COLORS['warning'])
            tree.tag_configure("ok", foreground=COLORS['text_muted'])
            tree.tag_configure("missing", foreground=COLORS['error'])
            tree.tag_configure("manual", foreground=COLORS['accent'], font=("Segoe UI", 10, "bold"))
            
            status_var.set(f"Found {len(tree.get_children())} footprints | {len(manual_selections)} manual selections")
        
        # Buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=12, pady=(0,12))
        
        status_var = tk.StringVar(value="Click 'Scan' to analyze footprints")
        ttk.Label(btn_frame, textvariable=status_var, foreground=COLORS['text_muted']).pack(side="left", padx=8)
        
        def select_all():
            for item in tree.get_children():
                tree.item(item, text="☑")
                checked_items[item] = True
            status_var.set(f"Selected all {len(tree.get_children())} footprints")
        
        def select_none():
            for item in tree.get_children():
                tree.item(item, text="☐")
                checked_items[item] = False
            status_var.set("Deselected all footprints")
        
        def apply_fixes():
            to_fix = []
            for item, checked in checked_items.items():
                if checked:
                    values = tree.item(item)["values"]
                    fp_name = values[0]
                    to_fix.append(fp_name)
            
            if not to_fix:
                messagebox.showwarning("Nothing Selected", "Please select footprints to update")
                return
            
            if not messagebox.askyesno("Confirm", 
                f"Apply 3D model fixes to {len(to_fix)} selected footprints?\n\n"
                "Your rotation/scale/offset settings will be preserved."):
                return
            
            # Apply fixes
            all_models_idx = _available_models_index(models_root)
            updated = 0
            
            for fp_name in to_fix:
                mod_file = fp_dir / f"{fp_name}.kicad_mod"
                if not mod_file.exists():
                    continue
                
                txt = mod_file.read_text(encoding="utf-8", errors="ignore")
                orig = txt
                
                # Check for manual selection first
                if fp_name in manual_selections:
                    # Use manually selected model
                    model_path = manual_selections[fp_name]
                    
                    # Remove existing models
                    txt = re.sub(r'\s*\(model\s+(?:"[^"]+"|[^\s)]+).*?\)', '', txt, flags=re.DOTALL)
                    
                    # Add the manual model
                    if rel.get():
                        try:
                            rel_path = os.path.relpath(model_path, start=fp_dir).replace("\\", "/")
                        except:
                            rel_path = model_path.name
                        model_str = f'(model "{rel_path}")'
                    else:
                        sub = "step" if model_path.suffix.lower() in (".step", ".stp") else "wrl"
                        model_str = f'(model "${{PERSONAL_LIB_DIR}}/3d/{sub}/{model_path.name}")'
                    
                    if txt.rstrip().endswith(')'):
                        txt = txt.rstrip()[:-1] + f"\n  {model_str}\n)"
                    else:
                        txt = txt + f"\n  {model_str}\n"
                else:
                    # Use auto-matching
                    # Rewrite existing models
                    def chooser(path_str):
                        return _choose_local_model_smart(all_models_idx, path_str, "step")
                    
                    if rel.get():
                        def make_rel(local_path):
                            try:
                                return os.path.relpath(local_path, start=fp_dir).replace("\\", "/")
                            except:
                                return local_path.name
                        txt = _rewrite_all_models(txt, chooser, make_rel)
                    else:
                        txt = _rewrite_all_models(txt, chooser, None)
                    
                    # Add missing?
                    if add_missing.get() and not re.search(r'\(model\s+', txt):
                        best = _choose_local_model_smart(all_models_idx, mod_file.stem, "step")
                        if best:
                            if rel.get():
                                try:
                                    rel_path = os.path.relpath(best, start=fp_dir).replace("\\", "/")
                                except:
                                    rel_path = best.name
                                model_str = f'(model "{rel_path}")'
                            else:
                                sub = "step" if best.suffix.lower() in (".step", ".stp") else "wrl"
                                model_str = f'(model "${{PERSONAL_LIB_DIR}}/3d/{sub}/{best.name}")'
                            
                            if txt.rstrip().endswith(')'):
                                txt = txt.rstrip()[:-1] + f"\n  {model_str}\n)"
                            else:
                                txt = txt + f"\n  {model_str}\n"
                
                if txt != orig:
                    mod_file.write_text(txt, encoding="utf-8")
                    updated += 1
            
            manual_count = sum(1 for fp in to_fix if fp in manual_selections)
            auto_count = updated - manual_count
            
            msg = f"Updated {updated} footprints!\n\n"
            if manual_count > 0:
                msg += f"• {manual_count} with manually selected models\n"
            if auto_count > 0:
                msg += f"• {auto_count} with auto-matched models\n"
            msg += "\nRotations/scales preserved."
            
            messagebox.showinfo("Success", msg)
            win.destroy()
        
        # Button layout: Scan on left, selection helpers in middle, Apply on right (most prominent)
        ttk.Button(btn_frame, text="🔍 Scan Footprints", command=scan_footprints).pack(side="left", padx=4)
        
        # Selection helpers in middle
        ttk.Button(btn_frame, text="Select All", command=select_all).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Select None", command=select_none).pack(side="left", padx=4)
        
        # APPLY button - make it HUGE and impossible to miss
        apply_btn = ttk.Button(btn_frame, text="✅ APPLY CHANGES TO CHECKED FOOTPRINTS", 
                              command=apply_fixes, style="Accent.TButton")
        apply_btn.pack(side="right", padx=12, ipadx=20, ipady=8)
        
        # Add tooltip-style hint
        hint_label = ttk.Label(btn_frame, text="← Click here after checking items!", 
                              foreground=COLORS['warning'], font=("Segoe UI", 9, "italic"))
        hint_label.pack(side="right", padx=4)
        
        # Auto-scan on open
        win.after(100, scan_footprints)
    
    def delete_parts_gui(self):
        """Delete parts from library."""
        sym_path = self.current_sym_path()
        if not sym_path.exists():
            messagebox.showerror("Error", f"Symbol file not found: {sym_path}")
            return
        
        # Load symbols
        try:
            symbols = list_symbols(sym_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load symbols:\n{e}")
            return
        
        if not symbols:
            messagebox.showinfo("No Parts", "Library is empty - no parts to delete.")
            return
        
        # Create dialog
        win = tk.Toplevel(self)
        win.title("Delete Parts from Library")
        win.geometry("1100x700")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        
        # Header
        header_frame = ttk.Frame(win)
        header_frame.pack(fill="x", padx=12, pady=12)
        
        ttk.Label(header_frame, text="Delete Parts from Library",
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        
        # Info
        info_frame = ttk.Frame(win)
        info_frame.pack(fill="x", padx=12, pady=(0,12))
        
        total_var = tk.StringVar(value=f"Total: {len(symbols)} parts")
        selected_var = tk.StringVar(value="Selected: 0 parts")
        
        ttk.Label(info_frame, textvariable=total_var,
                 foreground=COLORS['text_muted']).pack(side="left", padx=(0,20))
        ttk.Label(info_frame, textvariable=selected_var,
                 foreground=COLORS['warning']).pack(side="left")
        
        # Search
        search_frame = ttk.Frame(win)
        search_frame.pack(fill="x", padx=12, pady=(0,12))
        
        ttk.Label(search_frame, text="🔍 Filter:").pack(side="left", padx=(0,8))
        
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var, width=40)
        search_entry.pack(side="left", padx=(0,12))
        
        # List frame
        list_frame = ttk.Labelframe(win, text="Select Parts to Delete", padding=12)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        # Tree with checkboxes
        cols = ("delete", "symbol", "footprint", "mpn", "pins")
        tree = ttk.Treeview(list_frame, columns=cols, show="tree headings", height=20)
        tree.pack(side="left", fill="both", expand=True)
        
        # Scrollbar
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        vsb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=vsb.set)
        
        # Configure columns
        tree.heading("#0", text="☐", anchor="center")
        tree.heading("delete", text="")
        tree.heading("symbol", text="Symbol Name")
        tree.heading("footprint", text="Footprint")
        tree.heading("mpn", text="MPN")
        tree.heading("pins", text="Pins")
        
        tree.column("#0", width=40, anchor="center", stretch=False)
        tree.column("delete", width=0, stretch=False)  # Hidden
        tree.column("symbol", width=250, anchor="w")
        tree.column("footprint", width=220, anchor="w")
        tree.column("mpn", width=180, anchor="w")
        tree.column("pins", width=80, anchor="center")
        
        # Store symbol data
        symbol_data = {}
        selected_items = set()
        
        def populate_tree(filter_text=""):
            """Populate tree with symbols."""
            for item in tree.get_children():
                tree.delete(item)
            
            filter_lower = filter_text.lower()
            
            for name, block in symbols:
                # Apply filter
                if filter_text and filter_lower not in name.lower():
                    continue
                
                # Get symbol info
                fp = get_symbol_footprint(block) or ""
                
                # Get MPN
                mpn_match = re.search(r'\(\s*property\s+"MPN"\s+"([^"]*)"', block)
                mpn = mpn_match.group(1) if mpn_match else ""
                
                # Count pins
                pins = len(re.findall(r'\(pin\s+', block))
                
                # Store data
                symbol_data[name] = block
                
                # Add to tree
                is_selected = name in selected_items
                checkbox = "☑" if is_selected else "☐"
                
                item_id = tree.insert("", "end", text=checkbox, values=(
                    "1" if is_selected else "0",
                    name,
                    fp,
                    mpn,
                    pins
                ))
                
                # Tag for checkbox state
                if is_selected:
                    tree.item(item_id, tags=("checked",))
        
        def toggle_selection(event):
            """Toggle checkbox on click."""
            region = tree.identify_region(event.x, event.y)
            if region == "tree":
                item = tree.identify_row(event.y)
                if item:
                    symbol_name = tree.item(item, "values")[1]
                    
                    if symbol_name in selected_items:
                        selected_items.remove(symbol_name)
                        tree.item(item, text="☐", values=("0",) + tree.item(item, "values")[1:])
                        tree.item(item, tags=())
                    else:
                        selected_items.add(symbol_name)
                        tree.item(item, text="☑", values=("1",) + tree.item(item, "values")[1:])
                        tree.item(item, tags=("checked",))
                    
                    selected_var.set(f"Selected: {len(selected_items)} parts for deletion")
        
        tree.bind("<Button-1>", toggle_selection)
        
        def on_search(*args):
            """Filter tree on search."""
            populate_tree(search_var.get())
        
        search_var.trace_add("write", on_search)
        
        # Selection buttons
        btn_frame1 = ttk.Frame(win)
        btn_frame1.pack(fill="x", padx=12, pady=(0,12))
        
        def select_all():
            for name in symbol_data.keys():
                selected_items.add(name)
            populate_tree(search_var.get())
            selected_var.set(f"Selected: {len(selected_items)} parts for deletion")
        
        def select_none():
            selected_items.clear()
            populate_tree(search_var.get())
            selected_var.set("Selected: 0 parts")
        
        def select_filtered():
            filter_text = search_var.get().lower()
            if not filter_text:
                messagebox.showinfo("Info", "Enter a filter first to select matching parts")
                return
            
            for name in symbol_data.keys():
                if filter_text in name.lower():
                    selected_items.add(name)
            
            populate_tree(search_var.get())
            selected_var.set(f"Selected: {len(selected_items)} parts for deletion")
        
        ttk.Button(btn_frame1, text="Select All", command=select_all).pack(side="left", padx=4)
        ttk.Button(btn_frame1, text="Select None", command=select_none).pack(side="left", padx=4)
        ttk.Button(btn_frame1, text="Select Filtered", command=select_filtered).pack(side="left", padx=4)
        
        # Warning
        warning_frame = ttk.Frame(win)
        warning_frame.pack(fill="x", padx=12, pady=(0,12))
        
        ttk.Label(warning_frame, 
                 text="⚠️  Warning: Deleted parts cannot be recovered (except from backup file)",
                 foreground=COLORS['error']).pack(anchor="w")
        
        # Action buttons
        btn_frame2 = ttk.Frame(win)
        btn_frame2.pack(fill="x", padx=12, pady=(0,12))
        
        def do_delete():
            if not selected_items:
                messagebox.showwarning("Nothing Selected", "Please select at least one part to delete.")
                return
            
            if not messagebox.askyesno("Confirm Deletion",
                f"This will permanently delete {len(selected_items)} parts from the library.\n\n"
                f"A backup will be created (.bak)\n\n"
                f"Continue?",
                icon='warning'):
                return
            
            try:
                # Create backup
                backup_path = sym_path.with_suffix('.kicad_sym.bak')
                shutil.copy2(sym_path, backup_path)
                
                # Build keep dict (all except selected)
                keep = {}
                for name, block in symbols:
                    if name not in selected_items:
                        keep[name] = block
                
                # Rebuild library
                rebuild_symbols(sym_path, keep, backup=False)  # Already backed up
                
                messagebox.showinfo("Success!",
                    f"Deleted {len(selected_items)} parts from library.\n\n"
                    f"Remaining: {len(keep)} parts\n"
                    f"Backup: {backup_path.name}\n\n"
                    f"Library browser will refresh.")
                
                win.destroy()
                self.refresh_library_browser()
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete parts:\n{e}")
        
        ttk.Label(btn_frame2, text=f"Ready to delete selected parts",
                 foreground=COLORS['text_muted']).pack(side="left", padx=8)
        ttk.Button(btn_frame2, text="🗑️ Delete Selected Parts", command=do_delete,
                  style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(btn_frame2, text="Cancel", command=win.destroy).pack(side="right", padx=4)
        
        # Initial populate
        populate_tree()
        selected_var.set("Selected: 0 parts")
    
    def clean_duplicates_gui(self):
        """Clean duplicate symbols from library."""
        sym_path = self.current_sym_path()
        if not sym_path.exists():
            messagebox.showerror("Error", f"Symbol file not found: {sym_path}")
            return
        
        # Analyze for duplicates
        try:
            content = sym_path.read_text(encoding='utf-8')
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read library:\n{e}")
            return
        
        # Find all symbols
        symbol_pattern = r'\(symbol "([^"]+)"'
        symbols = re.findall(symbol_pattern, content)
        
        # Group by base name
        from collections import defaultdict
        base_groups = defaultdict(list)
        
        def get_base_name(symbol_name):
            """Extract base name from symbol (remove _imp1, _1_1, etc.)"""
            base = symbol_name
            base = re.sub(r'_imp\d+$', '', base)
            base = re.sub(r'_\d+_\d+$', '', base)
            return base
        
        for sym in symbols:
            base = get_base_name(sym)
            base_groups[base].append(sym)
        
        # Find duplicates
        duplicates = {k: v for k, v in base_groups.items() if len(v) > 1}
        
        if not duplicates:
            messagebox.showinfo("No Duplicates", 
                               f"Library is clean!\n\n"
                               f"Found {len(symbols)} symbols, all unique.")
            return
        
        # Create dialog
        win = tk.Toplevel(self)
        win.title("Clean Duplicate Symbols")
        win.geometry("1000x700")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        
        # Info
        info_frame = ttk.Frame(win)
        info_frame.pack(fill="x", padx=12, pady=12)
        
        total_dupes = sum(len(versions) - 1 for versions in duplicates.values())
        
        ttk.Label(info_frame, 
                 text=f"Found {len(duplicates)} symbol groups with duplicates\n"
                      f"Total: {len(symbols)} symbols | Unique: {len(base_groups)} | Duplicates: {total_dupes}",
                 font=("Segoe UI", 11)).pack()
        
        # List
        list_frame = ttk.Labelframe(win, text="Duplicate Groups (Keeping Originals)", padding=12)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        # Tree
        cols = ("base", "count", "versions", "will_keep", "will_delete")
        tree = ttk.Treeview(list_frame, columns=cols, show="headings")
        tree.pack(side="left", fill="both", expand=True)
        
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        vsb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=vsb.set)
        
        # Configure columns
        tree.heading("base", text="Symbol Base Name")
        tree.heading("count", text="# Versions")
        tree.heading("versions", text="All Versions")
        tree.heading("will_keep", text="✅ Will Keep")
        tree.heading("will_delete", text="🗑️ Will Delete")
        
        tree.column("base", width=200, anchor="w")
        tree.column("count", width=80, anchor="center")
        tree.column("versions", width=300, anchor="w")
        tree.column("will_keep", width=150, anchor="w")
        tree.column("will_delete", width=200, anchor="w")
        
        # Populate tree
        selections = {}
        for base, versions in sorted(duplicates.items()):
            # ALWAYS keep original (base name without suffix)
            if base in versions:
                keep = base
                delete = [v for v in versions if v != base]
            else:
                # No base? Keep first (unusual)
                keep = versions[0]
                delete = versions[1:]
            
            selections[base] = keep
            
            tree.insert("", "end", values=(
                base,
                len(versions),
                ", ".join(versions)[:50] + "..." if len(", ".join(versions)) > 50 else ", ".join(versions),
                keep,
                ", ".join(delete)[:30] + "..." if len(", ".join(delete)) > 30 else ", ".join(delete)
            ))
        
        # Explanation
        explain_frame = ttk.Frame(win)
        explain_frame.pack(fill="x", padx=12, pady=(0,12))
        
        ttk.Label(explain_frame, 
                 text="💡 Keeping originals because:\n"
                      "   • Schematics reference original names\n"
                      "   • Your modifications are in the originals\n"
                      "   • _imp versions are vanilla re-imports",
                 foreground=COLORS['text_muted']).pack(anchor="w")
        
        # Buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=12, pady=(0,12))
        
        def do_clean():
            nonlocal content  # Fix: content is modified in this function
            
            if not messagebox.askyesno("Confirm Cleanup",
                f"This will:\n"
                f"• Keep {len(duplicates)} original symbols\n"
                f"• Delete {total_dupes} duplicate symbols\n"
                f"• Create backup (.bak)\n\n"
                f"Continue?"):
                return
            
            try:
                # Create backup
                backup_path = sym_path.with_suffix('.kicad_sym.bak')
                shutil.copy2(sym_path, backup_path)
                
                # Build list of symbols to delete
                to_delete = set()
                for base, versions in duplicates.items():
                    keep = selections[base]
                    to_delete.update(v for v in versions if v != keep)
                
                # Remove symbols
                deleted_count = 0
                for symbol_name in to_delete:
                    # Find symbol block
                    pattern = rf'\(symbol "{re.escape(symbol_name)}".*?\n  \)\n(?=  \(symbol |\)\n$)'
                    match = re.search(pattern, content, re.DOTALL)
                    if match:
                        content = content[:match.start()] + content[match.end():]
                        deleted_count += 1
                
                # Write cleaned library
                sym_path.write_text(content, encoding='utf-8')
                
                messagebox.showinfo("Success!",
                    f"Library cleaned successfully!\n\n"
                    f"✅ Deleted: {deleted_count} duplicates\n"
                    f"✅ Kept: {len(duplicates)} originals\n"
                    f"✅ Backup: {backup_path.name}\n\n"
                    f"Refresh library browser to see changes.")
                
                win.destroy()
                self.refresh_library_browser()
                
            except Exception as e:
                messagebox.showerror("Error", f"Cleanup failed:\n{e}")
        
        ttk.Label(btn_frame, text=f"Ready to clean {total_dupes} duplicates",
                 foreground=COLORS['text_muted']).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="🗑️ Clean Library", command=do_clean,
                  style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="right", padx=4)
    
    def validate_files_gui(self):
        """Validate symbol and footprint files for syntax errors."""
        sym_path = self.current_sym_path()
        fp_dir = self.current_fp_dir()
        
        # Create dialog
        win = tk.Toplevel(self)
        win.title("Validate Files")
        win.geometry("1000x700")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        
        # Header
        header_frame = ttk.Frame(win)
        header_frame.pack(fill="x", padx=12, pady=12)
        
        ttk.Label(header_frame, text="File Validator",
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        
        # Info
        info_frame = ttk.Frame(win)
        info_frame.pack(fill="x", padx=12, pady=(0,12))
        
        ttk.Label(info_frame, 
                 text="Checks for: Syntax errors, mismatched parentheses, literal \\n characters, invalid formatting",
                 foreground=COLORS['text_muted']).pack(anchor="w")
        
        # Output
        output_frame = ttk.Labelframe(win, text="Validation Results", padding=12)
        output_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        output = ScrolledText(output_frame, wrap="word",
                              bg=COLORS['panel'], fg=COLORS['text'],
                              font=("Consolas", 10))
        output.pack(fill="both", expand=True)
        
        # Status
        status_frame = ttk.Frame(win)
        status_frame.pack(fill="x", padx=12, pady=(0,12))
        
        status_var = tk.StringVar(value="Click 'Run Validation' to check files")
        ttk.Label(status_frame, textvariable=status_var,
                 foreground=COLORS['text_muted']).pack(side="left")
        
        def log(msg, level="INFO"):
            colors = {
                "INFO": COLORS['text'],
                "SUCCESS": COLORS['success'],
                "WARNING": COLORS['warning'],
                "ERROR": COLORS['error']
            }
            output.insert("end", f"{msg}\n")
            output.see("end")
            win.update()
        
        def validate():
            output.delete("1.0", "end")
            errors = []
            warnings = []
            
            log("=" * 70)
            log("STARTING VALIDATION")
            log("=" * 70 + "\n")
            
            # Validate symbol file
            if sym_path.exists():
                log(f"📚 Checking: {sym_path.name}", "INFO")
                try:
                    content = sym_path.read_text(encoding='utf-8')
                    
                    # Check for literal \n
                    if r'\n' in content:
                        errors.append(f"Symbol file contains literal \\n characters")
                        log("  ❌ ERROR: Contains literal \\n characters", "ERROR")
                    
                    # Check parentheses balance
                    open_count = content.count('(')
                    close_count = content.count(')')
                    if open_count != close_count:
                        errors.append(f"Symbol file has mismatched parentheses: {open_count} open, {close_count} close")
                        log(f"  ❌ ERROR: Mismatched parentheses ({open_count} vs {close_count})", "ERROR")
                    else:
                        log(f"  ✅ Parentheses balanced ({open_count} pairs)", "SUCCESS")
                    
                    # Check for basic structure
                    if not content.startswith('(kicad_symbol_lib'):
                        errors.append("Symbol file doesn't start with (kicad_symbol_lib")
                        log("  ❌ ERROR: Invalid file header", "ERROR")
                    else:
                        log("  ✅ Valid file header", "SUCCESS")
                    
                    # Count symbols
                    symbol_count = len(re.findall(r'\(symbol "([^"]+)"', content))
                    log(f"  ℹ️  Found {symbol_count} symbols", "INFO")
                    
                except Exception as e:
                    errors.append(f"Failed to read symbol file: {e}")
                    log(f"  ❌ ERROR: {e}", "ERROR")
            else:
                warnings.append("Symbol file not found")
                log(f"⚠️  Symbol file not found: {sym_path}", "WARNING")
            
            log("")
            
            # Validate footprints
            if fp_dir.exists():
                log(f"👣 Checking footprints: {fp_dir.name}", "INFO")
                
                footprints = list(fp_dir.glob("*.kicad_mod"))
                if not footprints:
                    warnings.append("No footprint files found")
                    log("  ⚠️  No footprint files found", "WARNING")
                else:
                    log(f"  ℹ️  Found {len(footprints)} footprint files\n", "INFO")
                    
                    broken_fps = []
                    checked = 0
                    
                    for fp in footprints:
                        try:
                            content = fp.read_text(encoding='utf-8')
                            has_error = False
                            
                            # Check for literal \n
                            if r'\n' in content:
                                errors.append(f"{fp.name}: Contains literal \\n characters")
                                log(f"  ❌ {fp.name}: Literal \\n characters", "ERROR")
                                broken_fps.append(fp.name)
                                has_error = True
                            
                            # Check parentheses
                            open_count = content.count('(')
                            close_count = content.count(')')
                            if open_count != close_count:
                                errors.append(f"{fp.name}: Mismatched parentheses")
                                log(f"  ❌ {fp.name}: Mismatched parentheses ({open_count} vs {close_count})", "ERROR")
                                broken_fps.append(fp.name)
                                has_error = True
                            
                            # Check header
                            if not content.startswith('(footprint'):
                                errors.append(f"{fp.name}: Invalid header")
                                log(f"  ❌ {fp.name}: Invalid header", "ERROR")
                                broken_fps.append(fp.name)
                                has_error = True
                            
                            checked += 1
                            
                        except Exception as e:
                            errors.append(f"{fp.name}: {e}")
                            log(f"  ❌ {fp.name}: {e}", "ERROR")
                            broken_fps.append(fp.name)
                    
                    log("")
                    log(f"  ✅ Checked {checked} footprint files", "INFO")
                    if broken_fps:
                        log(f"  ❌ Found {len(broken_fps)} files with errors", "ERROR")
                    else:
                        log(f"  ✅ All footprints OK!", "SUCCESS")
            else:
                warnings.append("Footprint directory not found")
                log(f"⚠️  Footprint directory not found: {fp_dir}", "WARNING")
            
            # Summary
            log("\n" + "=" * 70)
            log("VALIDATION SUMMARY")
            log("=" * 70)
            
            if not errors and not warnings:
                log("\n✅ ALL FILES VALID! No issues found.", "SUCCESS")
                status_var.set("✅ Validation complete - All files OK!")
            else:
                if errors:
                    log(f"\n❌ ERRORS: {len(errors)}", "ERROR")
                    for err in errors[:10]:  # Show first 10
                        log(f"   • {err}", "ERROR")
                    if len(errors) > 10:
                        log(f"   ... and {len(errors)-10} more errors", "ERROR")
                
                if warnings:
                    log(f"\n⚠️  WARNINGS: {len(warnings)}", "WARNING")
                    for warn in warnings:
                        log(f"   • {warn}", "WARNING")
                
                status_var.set(f"❌ Found {len(errors)} errors, {len(warnings)} warnings")
            
            log("\n💡 Tip: Use 'Fix 3D Paths' to repair footprints with literal \\n characters")
        
        ttk.Button(status_frame, text="🔍 Run Validation", command=validate,
                  style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(status_frame, text="Close", command=win.destroy).pack(side="right", padx=4)
    
    def manual_3d_assign_gui(self):
        """Manually assign 3D models to footprints."""
        fp_dir = self.current_fp_dir()
        personal = Path(self.personal.get()).expanduser()
        
        if not fp_dir.exists():
            messagebox.showerror("Error", f"Footprint directory not found: {fp_dir}")
            return
        
        # Get all footprints
        footprints = sorted(list(fp_dir.glob("*.kicad_mod")))
        if not footprints:
            messagebox.showinfo("No Footprints", "No footprints found in library.")
            return
        
        # Create dialog
        win = tk.Toplevel(self)
        win.title("Manually Assign 3D Models")
        win.geometry("1200x800")
        win.configure(bg=COLORS['bg'])
        win.transient(self)
        
        # Header
        header_frame = ttk.Frame(win)
        header_frame.pack(fill="x", padx=12, pady=12)
        
        ttk.Label(header_frame, text="Manually Assign 3D Models to Footprints",
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        
        # Info
        info_frame = ttk.Frame(win)
        info_frame.pack(fill="x", padx=12, pady=(0,12))
        
        ttk.Label(info_frame, 
                 text="Select footprints, browse for 3D model, and assign. Useful when auto-pairing fails.",
                 foreground=COLORS['text_muted']).pack(anchor="w")
        
        # Main split frame
        main_frame = ttk.Frame(win)
        main_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)
        
        # Left: Footprint selection
        left_frame = ttk.Labelframe(main_frame, text="Select Footprints", padding=12)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0,6))
        
        # Search
        search_frame = ttk.Frame(left_frame)
        search_frame.pack(fill="x", pady=(0,8))
        
        ttk.Label(search_frame, text="🔍 Filter:").pack(side="left", padx=(0,8))
        search_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=search_var, width=30).pack(side="left", fill="x", expand=True)
        
        # Footprint list
        fp_frame = ttk.Frame(left_frame)
        fp_frame.pack(fill="both", expand=True)
        
        cols = ("select", "footprint", "current_model")
        fp_tree = ttk.Treeview(fp_frame, columns=cols, show="tree headings", height=20)
        fp_tree.pack(side="left", fill="both", expand=True)
        
        vsb = ttk.Scrollbar(fp_frame, orient="vertical", command=fp_tree.yview)
        vsb.pack(side="right", fill="y")
        fp_tree.configure(yscrollcommand=vsb.set)
        
        # Configure columns
        fp_tree.heading("#0", text="☐", anchor="center")
        fp_tree.heading("select", text="")
        fp_tree.heading("footprint", text="Footprint Name")
        fp_tree.heading("current_model", text="Current 3D Model")
        
        fp_tree.column("#0", width=40, anchor="center", stretch=False)
        fp_tree.column("select", width=0, stretch=False)
        fp_tree.column("footprint", width=250, anchor="w")
        fp_tree.column("current_model", width=250, anchor="w")
        
        # Selection state
        selected_fps = set()
        
        def get_current_model(fp_path):
            """Get current 3D model from footprint."""
            try:
                content = fp_path.read_text(encoding='utf-8')
                match = re.search(r'\(model\s+"([^"]+)"', content)
                if match:
                    model_path = match.group(1)
                    # Extract just filename for display
                    return Path(model_path).name
                return "(none)"
            except:
                return "(error reading file)"

        def populate_fp_tree(filter_text=""):
            """Populate footprint tree."""
            for item in fp_tree.get_children():
                fp_tree.delete(item)
            
            filter_lower = filter_text.lower()
            
            for fp in footprints:
                if filter_text and filter_lower not in fp.stem.lower():
                    continue
                
                current_model = get_current_model(fp)
                is_selected = fp in selected_fps
                checkbox = "☑" if is_selected else "☐"
                
                fp_tree.insert("", "end", text=checkbox, values=(
                    "1" if is_selected else "0",
                    fp.stem,
                    current_model
                ))
        
        def toggle_fp_selection(event):
            """Toggle footprint selection."""
            region = fp_tree.identify_region(event.x, event.y)
            if region == "tree":
                item = fp_tree.identify_row(event.y)
                if item:
                    fp_name = fp_tree.item(item, "values")[1]
                    fp_path = fp_dir / f"{fp_name}.kicad_mod"
                    
                    if fp_path in selected_fps:
                        selected_fps.remove(fp_path)
                    else:
                        selected_fps.add(fp_path)
                    
                    populate_fp_tree(search_var.get())
                    update_selection_count()
        
        fp_tree.bind("<Button-1>", toggle_fp_selection)
        search_var.trace_add("write", lambda *a: populate_fp_tree(search_var.get()))
        
        # Selection buttons
        sel_btn_frame = ttk.Frame(left_frame)
        sel_btn_frame.pack(fill="x", pady=(8,0))
        
        def select_all():
            filter_text = search_var.get().lower()
            for fp in footprints:
                if not filter_text or filter_text in fp.stem.lower():
                    selected_fps.add(fp)
            populate_fp_tree(search_var.get())
            update_selection_count()
        
        def select_none():
            selected_fps.clear()
            populate_fp_tree(search_var.get())
            update_selection_count()
        
        ttk.Button(sel_btn_frame, text="Select All", command=select_all).pack(side="left", padx=2)
        ttk.Button(sel_btn_frame, text="Select None", command=select_none).pack(side="left", padx=2)
        
        selection_label = ttk.Label(sel_btn_frame, text="Selected: 0", foreground=COLORS['text_muted'])
        selection_label.pack(side="right", padx=8)
        
        def update_selection_count():
            selection_label.config(text=f"Selected: {len(selected_fps)}")
        
        # Right: Model selection and settings
        right_frame = ttk.Labelframe(main_frame, text="3D Model Settings", padding=12)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(6,0))
        
        # Model file selection
        model_frame = ttk.Frame(right_frame)
        model_frame.pack(fill="x", pady=(0,12))
        
        ttk.Label(model_frame, text="3D Model File:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0,8))
        
        model_path_var = tk.StringVar()
        model_entry = ttk.Entry(model_frame, textvariable=model_path_var, state="readonly")
        model_entry.pack(fill="x", pady=(0,8))
        
        def browse_model():
            initial_dir = personal / "3d" / "step"
            if not initial_dir.exists():
                initial_dir = personal / "3d"
            if not initial_dir.exists():
                initial_dir = personal
            
            filename = filedialog.askopenfilename(
                title="Select 3D Model",
                initialdir=initial_dir,
                filetypes=[
                    ("3D Models", "*.step *.stp *.wrl"),
                    ("STEP Files", "*.step *.stp"),
                    ("VRML Files", "*.wrl"),
                    ("All Files", "*.*")
                ]
            )
            
            if filename:
                model_path_var.set(filename)
                update_preview()
        
        ttk.Button(model_frame, text="📁 Browse for 3D Model...", command=browse_model).pack(anchor="w")
        
        # Path format options
        format_frame = ttk.Frame(right_frame)
        format_frame.pack(fill="x", pady=(0,12))
        
        ttk.Label(format_frame, text="Path Format:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0,8))
        
        path_format = tk.StringVar(value="personal_lib")
        
        ttk.Radiobutton(format_frame, text='${PERSONAL_LIB_DIR}/3d/... (recommended)', 
                       variable=path_format, value="personal_lib",
                       command=lambda: update_preview()).pack(anchor="w", pady=2)
        ttk.Radiobutton(format_frame, text="Relative path (..\\3d\\...)", 
                       variable=path_format, value="relative",
                       command=lambda: update_preview()).pack(anchor="w", pady=2)
        ttk.Radiobutton(format_frame, text="Absolute path (full path)", 
                       variable=path_format, value="absolute",
                       command=lambda: update_preview()).pack(anchor="w", pady=2)
        
        # Preview
        preview_frame = ttk.Labelframe(right_frame, text="Preview", padding=12)
        preview_frame.pack(fill="both", expand=True, pady=(0,12))
        
        preview_text = ScrolledText(preview_frame, wrap="word", height=15,
                                                 bg=COLORS['panel'], fg=COLORS['text'],
                                                 font=("Consolas", 9))
        preview_text.pack(fill="both", expand=True)
        
        def update_preview():
            """Update preview of what will be written."""
            preview_text.delete("1.0", "end")
            
            model_file = model_path_var.get()
            if not model_file:
                preview_text.insert("1.0", "Select a 3D model file to see preview...")
                return
            
            model_path = Path(model_file)
            
            # Generate path based on format
            if path_format.get() == "personal_lib":
                # Determine subdirectory
                if model_path.suffix.lower() in (".step", ".stp"):
                    path_str = f'${{PERSONAL_LIB_DIR}}/3d/step/{model_path.name}'
                else:
                    path_str = f'${{PERSONAL_LIB_DIR}}/3d/wrl/{model_path.name}'
            elif path_format.get() == "relative":
                # Relative from footprints.pretty
                try:
                    rel = os.path.relpath(model_path, start=fp_dir).replace("\\", "/")
                    path_str = rel
                except:
                    path_str = f"..\\3d\\{'step' if model_path.suffix.lower() in ('.step','.stp') else 'wrl'}\\{model_path.name}"
            else:  # absolute
                path_str = str(model_path).replace("\\", "/")
            
            # Show preview
            preview_text.insert("1.0", f"Will add to {len(selected_fps)} footprint(s):\n\n")
            preview_text.insert("end", f'  (model "{path_str}")\n\n')
            preview_text.insert("end", f"Model file: {model_path.name}\n")
            preview_text.insert("end", f"Format: {path_format.get()}\n")
            preview_text.insert("end", f"Selected footprints: {len(selected_fps)}\n")
        
        # Action buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=12, pady=(0,12))
        
        def do_assign():
            """Assign 3D model to selected footprints."""
            if not selected_fps:
                messagebox.showwarning("No Selection", "Please select at least one footprint.")
                return
            
            model_file = model_path_var.get()
            if not model_file:
                messagebox.showwarning("No Model", "Please select a 3D model file.")
                return
            
            model_path = Path(model_file)
            if not model_path.exists():
                messagebox.showerror("Error", f"Model file not found:\n{model_path}")
                return
            
            # Generate model path string
            if path_format.get() == "personal_lib":
                if model_path.suffix.lower() in (".step", ".stp"):
                    path_str = f'${{PERSONAL_LIB_DIR}}/3d/step/{model_path.name}'
                else:
                    path_str = f'${{PERSONAL_LIB_DIR}}/3d/wrl/{model_path.name}'
            elif path_format.get() == "relative":
                try:
                    rel = os.path.relpath(model_path, start=fp_dir).replace("\\", "/")
                    path_str = rel
                except:
                    path_str = f"..\\3d\\{'step' if model_path.suffix.lower() in ('.step','.stp') else 'wrl'}\\{model_path.name}"
            else:
                path_str = str(model_path).replace("\\", "/")
            
            model_str = f'(model "{path_str}")'
            
            # Confirm
            if not messagebox.askyesno("Confirm Assignment",
                f"Assign 3D model to {len(selected_fps)} footprint(s)?\n\n"
                f"Model: {model_path.name}\n"
                f"Path: {path_str}\n\n"
                f"This will update the footprint files."):
                return
            
            # Apply to footprints
            success_count = 0
            error_count = 0
            errors = []
            
            for fp in selected_fps:
                try:
                    content = fp.read_text(encoding='utf-8')
                    
                    # Remove existing model references (handles multi-line blocks)
                    blocks = _extract_model_blocks(content)
                    for start, end, _, _ in reversed(blocks):
                        ws_start = start
                        while ws_start > 0 and content[ws_start - 1] in (' ', '\t'):
                            ws_start -= 1
                        ws_end = end
                        if ws_end < len(content) and content[ws_end] == '\n':
                            ws_end += 1
                        content = content[:ws_start] + content[ws_end:]
                    
                    # Add new model
                    if content.rstrip().endswith(')'):
                        content = content.rstrip()[:-1] + f"\n  {model_str}\n)"
                    else:
                        content = content + f"\n  {model_str}\n"
                    
                    # Write back
                    fp.write_text(content, encoding='utf-8')
                    success_count += 1
                    
                except Exception as e:
                    error_count += 1
                    errors.append(f"{fp.name}: {e}")
            
            # Show results
            if error_count == 0:
                messagebox.showinfo("Success!",
                    f"Successfully assigned 3D model to {success_count} footprint(s)!\n\n"
                    f"Model: {model_path.name}")
                win.destroy()
                self.refresh_library_browser()
            else:
                msg = f"Partial success:\n\n"
                msg += f"✅ Updated: {success_count}\n"
                msg += f"❌ Errors: {error_count}\n\n"
                msg += "Errors:\n" + "\n".join(errors[:5])
                if len(errors) > 5:
                    msg += f"\n... and {len(errors)-5} more"
                messagebox.showwarning("Partial Success", msg)
        
        ttk.Label(btn_frame, text="Manually assign 3D models when auto-pairing fails",
                 foreground=COLORS['text_muted']).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="✨ Assign Model", command=do_assign,
                  style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="right", padx=4)
        
        # Initial populate
        populate_fp_tree()
        update_selection_count()
    
    def repair_library_gui(self):
        """Repair or restore library file."""
        sym_path = self.current_sym_path()
        
        # Create dialog
        win = tk.Toplevel(self)
        win.title("Repair/Restore Library")
        win.geometry("700x500")
        win.configure(bg=COLORS['bg'])
        
        # Header
        ttk.Label(win, text="Library Repair & Restore Tool", font=("Segoe UI", 14, "bold")).pack(pady=12)
        
        # Info
        info_frame = ttk.Labelframe(win, text="Current Library", padding=12)
        info_frame.pack(fill="x", padx=12, pady=8)
        
        ttk.Label(info_frame, text=f"File: {sym_path.name}").pack(anchor="w", pady=2)
        
        if sym_path.exists():
            txt = sym_path.read_text(encoding="utf-8", errors="ignore")
            syms = parse_symbol_blocks(txt)
            ttk.Label(info_frame, text=f"Symbols found: {len(syms)}", foreground=COLORS['success']).pack(anchor="w", pady=2)
            
            # Check for backup
            backup_path = sym_path.with_suffix('.kicad_sym.bak')
            if backup_path.exists():
                backup_txt = backup_path.read_text(encoding="utf-8", errors="ignore")
                backup_syms = parse_symbol_blocks(backup_txt)
                backup_time = time.ctime(backup_path.stat().st_mtime)
                ttk.Label(info_frame, text=f"Backup available: {len(backup_syms)} symbols ({backup_time})", 
                         foreground=COLORS['warning']).pack(anchor="w", pady=2)
            else:
                ttk.Label(info_frame, text="No backup found", foreground=COLORS['text_muted']).pack(anchor="w", pady=2)
        else:
            ttk.Label(info_frame, text="File not found!", foreground=COLORS['error']).pack(anchor="w", pady=2)
        
        # Options
        opt_frame = ttk.Labelframe(win, text="Repair Options", padding=12)
        opt_frame.pack(fill="both", expand=True, padx=12, pady=8)
        
        log_text = ScrolledText(opt_frame, wrap="word", height=15, bg=COLORS['panel'], fg=COLORS['text'], font=("Consolas", 9))
        log_text.pack(fill="both", expand=True)
        
        def log_msg(msg):
            log_text.insert("end", msg + "\n")
            log_text.see("end")
            win.update_idletasks()
        
        def restore_from_backup():
            """Restore library from backup file."""
            backup_path = sym_path.with_suffix('.kicad_sym.bak')
            if not backup_path.exists():
                messagebox.showerror("Error", "No backup file found!")
                return
            
            if not messagebox.askyesno("Confirm Restore", f"Restore {sym_path.name} from backup?\n\nThis will overwrite the current file."):
                return
            
            try:
                shutil.copy2(backup_path, sym_path)
                log_msg(f"✅ Restored from backup: {backup_path.name}")
                messagebox.showinfo("Success", "Library restored from backup!")
                self.refresh_library_browser()
                win.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to restore: {e}")
        
        def validate_and_repair():
            """Validate library structure and repair if needed."""
            if not sym_path.exists():
                messagebox.showerror("Error", f"File not found: {sym_path}")
                return
            
            log_msg(f"🔍 Analyzing {sym_path.name}...")
            
            try:
                txt = sym_path.read_text(encoding="utf-8", errors="ignore")
                
                # Check header
                if not txt.startswith("(kicad_symbol_lib"):
                    log_msg("❌ Missing or invalid header")
                else:
                    log_msg("✅ Header OK")
                
                # Parse symbols
                syms = parse_symbol_blocks(txt)
                log_msg(f"✅ Found {len(syms)} symbols")
                
                # Check parentheses balance
                open_count = txt.count('(')
                close_count = txt.count(')')
                if open_count != close_count:
                    log_msg(f"⚠️ Parentheses mismatch: {open_count} open, {close_count} close")
                else:
                    log_msg(f"✅ Parentheses balanced ({open_count} pairs)")
                
                # Check each symbol
                errors = []
                for name, block in syms:
                    # Check required fields
                    if not re.search(r'\(property\s+"Reference"', block):
                        errors.append(f"Symbol '{name}': Missing Reference property")
                    if not re.search(r'\(property\s+"Value"', block):
                        errors.append(f"Symbol '{name}': Missing Value property")
                    
                    # Check parentheses in block
                    block_open = block.count('(')
                    block_close = block.count(')')
                    if block_open != block_close:
                        errors.append(f"Symbol '{name}': Unbalanced parentheses ({block_open} vs {block_close})")
                
                if errors:
                    log_msg(f"\n⚠️ Found {len(errors)} issues:")
                    for err in errors[:10]:  # Show first 10
                        log_msg(f"  • {err}")
                    if len(errors) > 10:
                        log_msg(f"  ... and {len(errors)-10} more")
                else:
                    log_msg("\n✅ Library structure is valid!")
                    messagebox.showinfo("Validation Complete", f"Library is valid with {len(syms)} symbols")
                
            except Exception as e:
                log_msg(f"❌ Error during validation: {e}")
                messagebox.showerror("Validation Failed", str(e))
        
        def rebuild_library():
            """Rebuild library file from scratch."""
            if not messagebox.askyesno("Confirm Rebuild", "Rebuild library from parsed symbols?\n\nThis will rewrite the entire file with proper formatting."):
                return
            
            log_msg("🔄 Rebuilding library...")
            
            try:
                txt = sym_path.read_text(encoding="utf-8", errors="ignore")
                syms = parse_symbol_blocks(txt)
                
                # Create backup
                backup_path = sym_path.with_suffix('.kicad_sym.rebuild_bak')
                shutil.copy2(sym_path, backup_path)
                log_msg(f"📦 Backup created: {backup_path.name}")
                
                # Rebuild
                new_content = HEADER
                for name, block in syms:
                    new_content += block + "\n"
                new_content += ")\n"
                
                sym_path.write_text(new_content, encoding="utf-8")
                log_msg(f"✅ Rebuilt library with {len(syms)} symbols")
                
                messagebox.showinfo("Success", f"Library rebuilt with {len(syms)} symbols!")
                self.refresh_library_browser()
                
            except Exception as e:
                log_msg(f"❌ Rebuild failed: {e}")
                messagebox.showerror("Rebuild Failed", str(e))
        
        # Buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=12)
        
        ttk.Button(btn_frame, text="🔍 Validate Library", command=validate_and_repair).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🔄 Rebuild Library", command=rebuild_library).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="⏮️ Restore from Backup", command=restore_from_backup).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Close", command=win.destroy).pack(side="left", padx=4)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # BOM MANAGER METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def browse_bom_file(self):
        """Browse for Octopart BOM file."""
        filename = filedialog.askopenfilename(
            title="Select Octopart BOM",
            filetypes=[
                ("Excel files", "*.xlsx"),
                ("CSV files", "*.csv"),
                ("All files", "*.*")
            ]
        )
        if filename:
            self.bom_file_var.set(filename)
    
    def browse_sch_file(self):
        """Browse for KiCad schematic file."""
        filename = filedialog.askopenfilename(
            title="Select KiCad Schematic (main file)",
            filetypes=[
                ("KiCad Schematic", "*.kicad_sch"),
                ("KiCad Project", "*.kicad_pro"),
                ("All files", "*.*")
            ]
        )
        if filename:
            self.sch_file_var.set(filename)
    
    def load_bom_files(self):
        """Load and analyze BOM and schematic files."""
        bom_file = self.bom_file_var.get()
        sch_file = self.sch_file_var.get()
        
        if not bom_file or not sch_file:
            messagebox.showerror("Error", "Please select both BOM and schematic files")
            return
        
        self.bom_status_var.set("Loading files...")
        self.update_idletasks()
        
        # Clear tree
        for item in self.bom_tree.get_children():
            self.bom_tree.delete(item)
        
        try:
            # Parse Octopart BOM
            self.bom_status_var.set("Parsing Octopart BOM...")
            self.update_idletasks()
            bom_data = parse_octopart_bom(Path(bom_file))
            
            # Parse KiCad schematics (including hierarchical sheets)
            self.bom_status_var.set("Parsing KiCad schematics...")
            self.update_idletasks()
            sch_data = parse_kicad_project(Path(sch_file))
            
            # Match and preview
            self.bom_status_var.set("Matching components...")
            self.update_idletasks()
            self.bom_matches = match_bom_to_schematic(bom_data, sch_data)
            
            # Populate tree
            add_count = 0
            update_count = 0
            ok_count = 0
            missing_count = 0
            
            for match in self.bom_matches:
                if match['status'] == 'add':
                    tag = 'add'
                    action = '✅ Add MPN'
                    add_count += 1
                elif match['status'] == 'update':
                    tag = 'update'
                    action = '⚠️ Update'
                    update_count += 1
                elif match['status'] == 'ok':
                    tag = 'ok'
                    action = '✓ OK'
                    ok_count += 1
                elif match['status'] == 'missing':
                    tag = 'missing'
                    action = '❌ Not found'
                    missing_count += 1
                else:
                    tag = ''
                    action = match['status']
                
                self.bom_tree.insert("", "end", values=(
                    match['ref'],
                    match.get('value', ''),
                    match.get('footprint', ''),
                    match.get('current_mpn', '(none)'),
                    match.get('new_mpn', ''),
                    action,
                    Path(match.get('file', '')).name if match.get('file') else ''
                ), tags=(tag,))
            
            # Configure tags
            self.bom_tree.tag_configure('add', foreground=COLORS['success'])
            self.bom_tree.tag_configure('update', foreground=COLORS['warning'])
            self.bom_tree.tag_configure('ok', foreground=COLORS['text_muted'])
            self.bom_tree.tag_configure('missing', foreground=COLORS['error'])
            
            # Update summary
            total = len(self.bom_matches)
            files_count = len(set(m.get('file') for m in self.bom_matches if m.get('file')))
            
            summary = f"Found {total} components in {files_count} file(s): "
            summary += f"{add_count} to add, {update_count} to update, {ok_count} OK"
            if missing_count > 0:
                summary += f", {missing_count} not found"
            self.bom_summary_var.set(summary)
            
            if missing_count > 0:
                self.bom_status_var.set(f"⚠️ Warning: {missing_count} components in BOM not found in schematic")
            else:
                self.bom_status_var.set(f"✅ Analysis complete! Ready to apply changes.")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load files:\n\n{e}")
            self.bom_status_var.set(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    def apply_bom_updates(self):
        """Apply BOM updates to schematic files."""
        if not hasattr(self, 'bom_matches') or not self.bom_matches:
            messagebox.showerror("Error", "No changes to apply. Load files first.")
            return
        
        # Count changes
        to_apply = [m for m in self.bom_matches if m['status'] in ('add', 'update')]
        
        if not to_apply:
            messagebox.showinfo("No Changes", "No changes to apply. All components are up to date.")
            return
        
        if not messagebox.askyesno("Confirm", 
            f"Apply {len(to_apply)} changes to schematic files?\n\n"
            "Backups will be created with .bak extension."):
            return
        
        try:
            self.bom_status_var.set("Applying changes...")
            self.update_idletasks()
            
            result = apply_bom_to_schematics(self.bom_matches)
            
            messagebox.showinfo("Success", 
                f"Updated {result['updated']} components in {result['files_modified']} file(s)\n\n"
                f"Backups created with .bak extension")
            
            self.bom_status_var.set(f"✅ Applied {result['updated']} changes successfully!")
            
            # Reload to show current state
            self.load_bom_files()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply changes:\n\n{e}")
            import traceback
            traceback.print_exc()
    
    def export_bom_preview(self):
        """Export preview to CSV."""
        if not hasattr(self, 'bom_matches') or not self.bom_matches:
            messagebox.showerror("Error", "No data to export. Load files first.")
            return
        
        filename = filedialog.asksaveasfilename(
            title="Export Preview",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if not filename:
            return
        
        try:
            import csv
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Reference', 'Value', 'Footprint', 'Current MPN', 'New MPN', 'Action', 'File'])
                for match in self.bom_matches:
                    writer.writerow([
                        match['ref'],
                        match.get('value', ''),
                        match.get('footprint', ''),
                        match.get('current_mpn', ''),
                        match.get('new_mpn', ''),
                        match['status'],
                        Path(match.get('file', '')).name if match.get('file') else ''
                    ])
            
            messagebox.showinfo("Success", f"Exported to:\n{filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export:\n\n{e}")
    
    def standardize_mpn_gui(self):
        """Standardize MPN fields - find part numbers in various fields and move to 'MPN'."""
        sym_path = self.current_sym_path()
        if not sym_path.exists():
            messagebox.showerror("Error", f"Symbol file not found: {sym_path}")
            return
        
        # Create dialog
        win = tk.Toplevel(self)
        win.title("Standardize MPN Fields")
        win.geometry("900x700")
        win.configure(bg=COLORS['bg'])
        
        # Options
        opt_frame = ttk.Frame(win)
        opt_frame.pack(fill="x", padx=12, pady=12)
        
        dry_run = tk.BooleanVar(value=True)
        remove_old = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(opt_frame, text="Dry run (preview only)", variable=dry_run).pack(anchor="w", pady=4)
        ttk.Checkbutton(opt_frame, text="Remove old field after copying to MPN", variable=remove_old).pack(anchor="w", pady=4)
        
        info_lbl = ttk.Label(opt_frame, text="Searches for: Manufacturer_Part_Number, Mfr Part Number, Part Number, PartNumber, P/N, etc.\n📝 Fallback: If no part number field found, will use Symbol Name as MPN", 
                            foreground=COLORS['text_muted'], wraplength=800)
        info_lbl.pack(anchor="w", pady=8)
        
        # Results display
        results_lf = ttk.Labelframe(win, text="Results", padding=12)
        results_lf.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        cols = ("symbol", "old_field", "value", "action")
        tree = ttk.Treeview(results_lf, columns=cols, show="headings", height=20)
        tree.pack(fill="both", expand=True, side="left")
        
        vsb = ttk.Scrollbar(results_lf, orient="vertical", command=tree.yview)
        vsb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=vsb.set)
        
        tree.heading("symbol", text="Symbol")
        tree.heading("old_field", text="Original Field")
        tree.heading("value", text="Part Number")
        tree.heading("action", text="Action")
        
        tree.column("symbol", width=200)
        tree.column("old_field", width=200)
        tree.column("value", width=250)
        tree.column("action", width=200)
        
        # Summary label
        summary_var = tk.StringVar(value="Click 'Scan' to analyze library")
        summary_lbl = ttk.Label(win, textvariable=summary_var, foreground=COLORS['text_muted'])
        summary_lbl.pack(pady=8)
        
        # Buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=12)
        
        def scan():
            """Scan library for parts with non-standard MPN fields."""
            for item in tree.get_children():
                tree.delete(item)
            
            summary_var.set("Scanning...")
            win.update_idletasks()
            
            # Read symbol file
            txt = sym_path.read_text(encoding="utf-8")
            syms = list_symbols(sym_path)
            
            found_count = 0
            already_mpn = 0
            fallback_count = 0
            
            for name, block in syms:
                # Check if already has MPN field
                has_mpn = bool(re.search(r'\(property\s+"MPN"\s+"([^"]*)"', block))
                
                # Look for other part number fields
                mpn_patterns = [
                    ('Manufacturer_Part_Number', r'\(property\s+"Manufacturer_Part_Number"\s+"([^"]*)"'),
                    ('Mfr Part Number', r'\(property\s+"Mfr Part Number"\s+"([^"]*)"'),
                    ('Part Number', r'\(property\s+"Part Number"\s+"([^"]*)"'),
                    ('PartNumber', r'\(property\s+"PartNumber"\s+"([^"]*)"'),
                    ('P/N', r'\(property\s+"P/N"\s+"([^"]*)"'),
                    ('Mfr_PN', r'\(property\s+"Mfr_PN"\s+"([^"]*)"'),
                    ('ManufacturerPartNumber', r'\(property\s+"ManufacturerPartNumber"\s+"([^"]*)"'),
                ]
                
                found = False
                for field_name, pattern in mpn_patterns:
                    match = re.search(pattern, block, re.IGNORECASE)
                    if match:
                        value = match.group(1)
                        if value and value.strip():  # Has actual value
                            found = True
                            found_count += 1
                            
                            if has_mpn:
                                action = "⚠️ Already has MPN (would update)"
                                tag = "warn"
                            else:
                                action = "✅ Will copy to MPN"
                                tag = "ok"
                            
                            tree.insert("", "end", values=(name, field_name, value, action), tags=(tag,))
                            break
                
                # FALLBACK: Use symbol name if no part number found
                if not found and not has_mpn:
                    fallback_count += 1
                    found_count += 1
                    action = "📝 Will use Symbol Name"
                    tree.insert("", "end", values=(name, "Symbol Name (fallback)", name, action), tags=("fallback",))
                elif not found and has_mpn:
                    already_mpn += 1
            
            tree.tag_configure("ok", foreground=COLORS['success'])
            tree.tag_configure("warn", foreground=COLORS['warning'])
            tree.tag_configure("fallback", foreground=COLORS['text_muted'])
            
            summary_var.set(f"Found: {found_count} parts to standardize | Already OK: {already_mpn} | Using fallback: {fallback_count}")
        
        def apply():
            """Apply the standardization."""
            if dry_run.get():
                messagebox.showinfo("Dry Run", "Dry run mode - no changes made. Uncheck 'Dry run' to apply changes.")
                return
            
            if not messagebox.askyesno("Confirm", f"Standardize MPN fields in {sym_path.name}?\n\nThis will modify your library file."):
                return
            
            try:
                changed_count = standardize_mpn_fields(
                    sym_path, 
                    remove_old_field=remove_old.get(),
                    log=lambda *a: self.log_message(" ".join(str(x) for x in a), "INFO")
                )
                
                messagebox.showinfo("Success", f"Updated {changed_count} symbols with MPN field")
                self.refresh_library_browser()
                win.destroy()
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to standardize MPNs: {e}")
        
        ttk.Button(btn_frame, text="🔍 Scan Library", command=scan, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(btn_frame, text="✅ Apply Changes", command=apply).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Close", command=win.destroy).pack(side="left", padx=4)
        
        # Auto-scan
        win.after(100, scan)
    
    def set_kicad_var(self):
        """Set KiCad path variable."""
        personal = Path(self.personal.get()).expanduser()
        if not personal.exists():
            messagebox.showerror("Error", "Personal library path not set")
            return
        
        try:
            updated = _update_kicad_path_variable(personal, log=lambda *a: self.log_message(" ".join(str(x) for x in a), "INFO"))
            if updated:
                messagebox.showinfo("Success", f"Updated {len(updated)} KiCad config(s). Restart KiCad.")
            else:
                messagebox.showinfo("Info", "No KiCad configs found or already set")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def rewrite_fp_table(self):
        """Rewrite fp-lib-table to use variables."""
        personal = Path(self.personal.get()).expanduser()
        if not personal.exists():
            messagebox.showerror("Error", "Personal library path not set")
            return
        
        try:
            changed = _rewrite_fp_lib_table(personal, log=lambda *a: self.log_message(" ".join(str(x) for x in a), "INFO"))
            if changed:
                messagebox.showinfo("Success", f"Updated {len(changed)} fp-lib-table file(s)")
            else:
                messagebox.showinfo("Info", "No changes needed")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def rewrite_models(self):
        """Rewrite 3D model paths to be relative."""
        personal = Path(self.personal.get()).expanduser()
        if not personal.exists():
            messagebox.showerror("Error", "Personal library path not set")
            return
        
        try:
            count = _rewrite_models(personal, log=lambda *a: self.log_message(" ".join(str(x) for x in a), "INFO"))
            if count:
                messagebox.showinfo("Success", f"Updated {count} footprint(s)")
            else:
                messagebox.showinfo("Info", "No changes needed")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def show_about(self):
        """Show about dialog."""
        about = f"""
{APP_NAME} v{APP_VERSION}

Modern, portable library manager for KiCad.

Features:
• Import symbols, footprints, 3D models
• Smart duplicate detection
• Automatic 3D model pairing
• Library management tools
• Modern dark theme
• No external dependencies

© 2025 Community Contribution
License: MIT
"""
        messagebox.showinfo("About", about)

    
    # ═══════════════════════════════════════════════════════════════════════════
    # PREFERENCES
    # ═══════════════════════════════════════════════════════════════════════════
    
    def load_prefs(self):
        """Load preferences from JSON file."""
        if PREFS_FILE.exists():
            try:
                with open(PREFS_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_prefs(self):
        """Save preferences to JSON file."""
        prefs = {
            "last_source_dir": self.source.get(),
            "last_personal_dir": self.personal.get(),
            "sym_choice": self.sym_choice.get(),
            "fp_choice": self.fp_choice.get(),
            "models_root": self.models_root.get(),
            "rel_models": self.rel_models.get(),
            "footprint_nickname": self.nick.get(),
            "auto_detect_nickname": self.auto_nick.get(),
            "autoset": self.autoset.get(),
            "skip_imported": self.skip_imported.get(),
            "downloads_dir": self.downloads_dir.get(),
            "inbox_zips_only": self.inbox_zips_only.get(),
            "recent_sources": self.recent_sources,
            "recent_personal": self.recent_personal,
        }
        try:
            with open(PREFS_FILE, 'w') as f:
                json.dump(prefs, f, indent=2)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS (not in App class)
# ═══════════════════════════════════════════════════════════════════════════

def standardize_mpn_fields(sym_path: Path, remove_old_field: bool = False, log=lambda *a: None) -> int:
    """
    Standardize MPN fields in symbol library.
    Finds part numbers in various field names and copies/moves them to 'MPN' field.
    
    Returns count of symbols changed.
    """
    if not sym_path.exists():
        return 0
    
    # Read file
    txt = sym_path.read_text(encoding="utf-8")
    syms = list_symbols(sym_path)
    
    changed_count = 0
    new_blocks = []
    
    # Field patterns to search for - just extract the value
    mpn_patterns = [
        ('Manufacturer_Part_Number', r'\(property\s+"Manufacturer_Part_Number"\s+"([^"]*)"'),
        ('Mfr Part Number', r'\(property\s+"Mfr Part Number"\s+"([^"]*)"'),
        ('Part Number', r'\(property\s+"Part Number"\s+"([^"]*)"'),
        ('PartNumber', r'\(property\s+"PartNumber"\s+"([^"]*)"'),
        ('P/N', r'\(property\s+"P/N"\s+"([^"]*)"'),
        ('Mfr_PN', r'\(property\s+"Mfr_PN"\s+"([^"]*)"'),
        ('ManufacturerPartNumber', r'\(property\s+"ManufacturerPartNumber"\s+"([^"]*)"'),
    ]
    
    for name, block in syms:
        original_block = block
        
        # Check if already has MPN
        has_mpn = bool(re.search(r'\(property\s+"MPN"\s+"', block))
        
        # Look for other part number fields
        found_value = None
        found_field = None
        
        for field_name, pattern in mpn_patterns:
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                value = match.group(1)
                if value and value.strip():
                    found_value = value
                    found_field = field_name
                    break  # Found a part number
        
        # FALLBACK: If no part number field found, use symbol name
        if not found_value and not has_mpn:
            found_value = name
            found_field = "Symbol Name (fallback)"
        
        # Apply the value if we found one
        if found_value:
            if has_mpn:
                # Update existing MPN value
                log(f"{name}: Updating MPN from {found_field} = '{found_value}'")
                block = re.sub(
                    r'(\(property\s+"MPN"\s+)"[^"]*"',
                    rf'\1"{found_value}"',
                    block,
                    count=1
                )
            else:
                # Add new MPN field - insert after Reference property
                log(f"{name}: Adding MPN from {found_field} = '{found_value}'")
                
                # Find the complete Reference property block (with nested parens)
                ref_start = block.find('(property "Reference"')
                if ref_start != -1:
                    # Count parentheses to find where Reference property ends
                    paren_count = 0
                    pos = ref_start
                    ref_end = -1
                    
                    while pos < len(block):
                        if block[pos] == '(':
                            paren_count += 1
                        elif block[pos] == ')':
                            paren_count -= 1
                            if paren_count == 0:
                                ref_end = pos + 1
                                break
                        pos += 1
                    
                    if ref_end != -1:
                        # Insert after the complete Reference property
                        mpn_property = f'\n  (property "MPN" "{found_value}"\n    (at 0 0 0)\n    (effects\n      (font\n        (size 1.27 1.27)\n      )\n      (hide yes)\n    )\n  )'
                        block = block[:ref_end] + mpn_property + block[ref_end:]
                    else:
                        # Fallback: couldn't parse Reference property
                        log(f"{name}: WARNING - Could not parse Reference property")
                else:
                    # Fallback: No Reference property found, insert after symbol declaration
                    symbol_start = re.search(r'(\(symbol\s+"[^"]+"\s+)', block)
                    if symbol_start:
                        insert_point = symbol_start.end()
                        mpn_property = f'\n  (property "MPN" "{found_value}"\n    (at 0 0 0)\n    (effects\n      (font\n        (size 1.27 1.27)\n      )\n      (hide yes)\n    )\n  )'
                        block = block[:insert_point] + mpn_property + block[insert_point:]
            
            # Remove old field if requested (not for symbol name fallback)
            if remove_old_field and found_field != "Symbol Name (fallback)" and found_field != "MPN":
                # Find the original pattern for this field
                for fname, fpat in mpn_patterns:
                    if fname == found_field:
                        block = re.sub(fpat, '', block, flags=re.IGNORECASE)
                        log(f"{name}: Removed old field '{found_field}'")
                        break
            
            if block != original_block:
                changed_count += 1
        
        new_blocks.append((name, block))
    
    # Rebuild file if changes were made
    if changed_count > 0:
        # Write back to file - include ALL symbols (changed and unchanged)
        new_content = HEADER
        for name, block in new_blocks:
            new_content += block + "\n"
        new_content += ")\n"
        
        # Backup original
        backup_path = sym_path.with_suffix('.kicad_sym.bak')
        shutil.copy2(sym_path, backup_path)
        log(f"Backup created: {backup_path}")
        
        # Write new file
        sym_path.write_text(new_content, encoding="utf-8")
        log(f"Updated {changed_count} symbols in {sym_path.name}")
    else:
        log("No changes needed - all symbols already have MPN or no part numbers found")
    
    return changed_count


def _col_to_index(col: str) -> int:
    """Convert Excel column letter to index (A=1, B=2, ..., Z=26, AA=27)."""
    result = 0
    for i, char in enumerate(reversed(col)):
        result += (ord(char) - ord('A') + 1) * (26 ** i)
    return result


def _find_matching_paren(text: str, start: int) -> int:
    """
    Find the matching closing parenthesis for an opening paren at position start.
    Returns the index of the matching closing paren.
    """
    if text[start] != '(':
        return -1
    
    depth = 0
    i = start
    while i < len(text):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# ═══════════════════════════════════════════════════════════════════════════
# BOM MANAGER HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def parse_octopart_bom(bom_path: Path) -> List[dict]:
    """
    Parse Octopart BOM from Excel or CSV.
    Returns list of components with flexible column name recognition.
    Uses fallback method for Excel files to avoid openpyxl compatibility issues.
    """
    # For CSV, use the stdlib reader (utf-8-sig strips Excel's BOM)
    if bom_path.suffix.lower() == '.csv':
        with open(bom_path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])
            data = list(reader)
    else:
        # For Excel, read XML directly to avoid openpyxl bugs
        import zipfile
        import xml.etree.ElementTree as ET
        
        with zipfile.ZipFile(bom_path, 'r') as zip_ref:
            # Read shared strings
            shared_strings = []
            try:
                with zip_ref.open('xl/sharedStrings.xml') as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    for si in root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
                        t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
                        if t is not None:
                            shared_strings.append(t.text if t.text else '')
            except:
                pass
            
            # Read first sheet
            with zip_ref.open('xl/worksheets/sheet1.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                
                rows_data = []
                for row_elem in root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'):
                    cells = {}
                    for cell in row_elem.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
                        cell_ref = cell.get('r')
                        col = ''.join(c for c in cell_ref if c.isalpha())
                        
                        v = cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                        if v is not None:
                            cell_type = cell.get('t')
                            if cell_type == 's':
                                try:
                                    cells[col] = shared_strings[int(v.text)]
                                except:
                                    cells[col] = v.text
                            else:
                                cells[col] = v.text
                        else:
                            cells[col] = ''
                    
                    rows_data.append(cells)
                
                # Convert to list of dicts with proper column names
                if not rows_data:
                    raise ValueError("No data found in Excel file")
                
                # First row is headers
                header_row = rows_data[0]
                sorted_headers = sorted(header_row.items(), key=lambda x: _col_to_index(x[0]))
                headers = [val for col, val in sorted_headers]
                
                # Data rows
                data = []
                for row_dict in rows_data[1:]:
                    row_data = {}
                    for col, header in zip([c for c, _ in sorted_headers], headers):
                        row_data[header] = row_dict.get(col, '')
                    data.append(row_data)
    
    # Flexible column name mapping (case-insensitive)
    col_map = {}
    for col in headers:
        col_lower = str(col).lower().strip()
        
        # Reference column
        if col_lower in ['reference', 'designator', 'ref', 'designators', 'part references']:
            col_map['reference'] = col
        
        # MPN column
        elif col_lower in ['matched part number', 'mpn', 'part number', 'manufacturer part number', 
                          'mfr part number', 'partnumber', 'mfr_pn']:
            col_map['mpn'] = col
        
        # Value column
        elif col_lower in ['value', 'component value', 'part value']:
            col_map['value'] = col
        
        # Footprint column
        elif col_lower in ['footprint', 'package', 'footprint name', 'pcb footprint']:
            col_map['footprint'] = col
        
        # Manufacturer column
        elif col_lower in ['matched manufacturer', 'manufacturer', 'mfr', 'manufacturer name']:
            col_map['manufacturer'] = col
        
        # Datasheet column
        elif col_lower in ['datasheet', 'datasheet link', 'datasheet url']:
            col_map['datasheet'] = col
        
        # Description column
        elif col_lower in ['description', 'part description', 'component description']:
            col_map['description'] = col
    
    # Check required columns
    if 'reference' not in col_map:
        raise ValueError(f"Required column 'Reference' (or 'Designator', 'Ref') not found in BOM. Available columns: {headers}")
    if 'mpn' not in col_map:
        raise ValueError(f"Required column 'Matched Part Number' (or 'MPN', 'Part Number') not found in BOM. Available columns: {headers}")
    
    # Parse rows
    components = []
    for row in data:
        ref_str = str(row.get(col_map['reference'], '')).strip()
        mpn = str(row.get(col_map['mpn'], '')).strip()
        
        if not ref_str or ref_str.lower() in ['nan', 'none', '']:
            continue
        
        # Split multiple references (e.g., "C1, C9, C18, C31")
        refs = [r.strip() for r in ref_str.replace(',', ' ').split() if r.strip()]
        
        for ref in refs:
            comp = {
                'ref': ref,
                'mpn': mpn if mpn.lower() not in ['nan', 'none', ''] else '',
                'value': str(row.get(col_map['value'], '')).strip() if 'value' in col_map else '',
                'footprint': str(row.get(col_map['footprint'], '')).strip() if 'footprint' in col_map else '',
                'manufacturer': str(row.get(col_map['manufacturer'], '')).strip() if 'manufacturer' in col_map else '',
                'datasheet': str(row.get(col_map['datasheet'], '')).strip() if 'datasheet' in col_map else '',
                'description': str(row.get(col_map['description'], '')).strip() if 'description' in col_map else '',
            }
            components.append(comp)
    
    return components


def parse_kicad_schematic(sch_path: Path) -> dict:
    """
    Parse a single KiCad schematic file.
    Returns dict with components and sub-sheet references.
    """
    if not sch_path.exists():
        return {'components': [], 'sheets': []}
    
    text = sch_path.read_text(encoding='utf-8')
    
    components = []
    sheets = []
    
    # Find all component symbols
    # Pattern: (symbol ... (property "Reference" "C1") ... )
    i = 0
    while True:
        # Find next symbol
        i = text.find('\n\t(symbol\n', i)
        if i == -1:
            break
        
        # Find matching closing paren
        depth = 0
        start = i + 1
        j = start
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        
        symbol_block = text[start:j+1]
        
        # Extract properties
        ref = re.search(r'\(property "Reference" "([^"]+)"', symbol_block)
        value = re.search(r'\(property "Value" "([^"]+)"', symbol_block)
        footprint = re.search(r'\(property "Footprint" "([^"]+)"', symbol_block)
        mpn = re.search(r'\(property "MPN" "([^"]+)"', symbol_block)
        
        if ref:
            comp = {
                'ref': ref.group(1),
                'value': value.group(1) if value else '',
                'footprint': footprint.group(1) if footprint else '',
                'mpn': mpn.group(1) if mpn else '',
                'block': symbol_block,
                'start': start,
                'end': j+1
            }
            components.append(comp)
        
        i = j + 1
    
    # Find sub-sheets
    # Pattern: (sheet ... (property "Sheetfile" "CM5_GPIO.kicad_sch") ... )
    for match in re.finditer(r'\(property "Sheetfile" "([^"]+)"', text):
        sheets.append(match.group(1))
    
    return {'components': components, 'sheets': sheets, 'file': sch_path}


def parse_kicad_project(entry_file: Path) -> dict:
    """
    Parse KiCad project starting from main schematic or project file.
    Recursively processes hierarchical sheets.
    Returns dict with all components from all sheets.
    """
    # If .kicad_pro, extract schematic path
    if entry_file.suffix == '.kicad_pro':
        import json
        proj_data = json.loads(entry_file.read_text(encoding='utf-8'))
        sch_name = proj_data.get('schematic', entry_file.stem + '.kicad_sch')
        entry_file = entry_file.parent / sch_name
    
    all_files = {}
    to_process = [entry_file]
    processed = set()
    
    while to_process:
        sch_file = to_process.pop(0)
        
        if sch_file in processed:
            continue
        processed.add(sch_file)
        
        data = parse_kicad_schematic(sch_file)
        all_files[str(sch_file)] = data
        
        # Add sub-sheets to process
        for sheet_name in data['sheets']:
            sheet_path = sch_file.parent / sheet_name
            if sheet_path.exists() and sheet_path not in processed:
                to_process.append(sheet_path)
    
    return all_files


def match_bom_to_schematic(bom_data: List[dict], sch_data: dict) -> List[dict]:
    """
    Match BOM components to schematic components.
    Returns list of matches with status (add/update/ok/missing).
    """
    # Build lookup of all schematic components
    sch_lookup = {}
    for file_path, file_data in sch_data.items():
        for comp in file_data['components']:
            sch_lookup[comp['ref']] = {
                'value': comp['value'],
                'footprint': comp['footprint'],
                'current_mpn': comp['mpn'],
                'file': file_path,
                'comp_data': comp
            }
    
    # Match BOM to schematic
    matches = []
    for bom_comp in bom_data:
        ref = bom_comp['ref']
        new_mpn = bom_comp['mpn']
        
        if ref in sch_lookup:
            sch_comp = sch_lookup[ref]
            current_mpn = sch_comp['current_mpn']
            
            if not current_mpn and new_mpn:
                status = 'add'
            elif current_mpn and new_mpn and current_mpn != new_mpn:
                status = 'update'
            elif current_mpn == new_mpn:
                status = 'ok'
            else:
                status = 'unknown'
            
            matches.append({
                'ref': ref,
                'value': sch_comp['value'],
                'footprint': sch_comp['footprint'],
                'current_mpn': current_mpn,
                'new_mpn': new_mpn,
                'manufacturer': bom_comp.get('manufacturer', ''),
                'datasheet': bom_comp.get('datasheet', ''),
                'description': bom_comp.get('description', ''),
                'status': status,
                'file': sch_comp['file'],
                'comp_data': sch_comp['comp_data']
            })
        else:
            # In BOM but not in schematic
            matches.append({
                'ref': ref,
                'value': bom_comp.get('value', ''),
                'footprint': bom_comp.get('footprint', ''),
                'current_mpn': '',
                'new_mpn': new_mpn,
                'status': 'missing',
                'file': ''
            })
    
    return matches


def apply_bom_to_schematics(matches: List[dict]) -> dict:
    """
    Apply BOM updates to schematic files.
    Creates backups and updates MPN properties.
    """
    files_to_update = {}
    
    # Group by file
    for match in matches:
        if match['status'] not in ('add', 'update'):
            continue
        
        file = match.get('file')
        if not file:
            continue
        
        if file not in files_to_update:
            files_to_update[file] = []
        files_to_update[file].append(match)
    
    updated_count = 0
    
    for file_path, updates in files_to_update.items():
        sch_path = Path(file_path)
        
        # Create backup
        backup_path = sch_path.with_suffix('.kicad_sch.bak')
        shutil.copy2(sch_path, backup_path)
        
        # Read file
        text = sch_path.read_text(encoding='utf-8')
        
        # Apply updates
        for update in updates:
            comp_data = update['comp_data']
            new_mpn = update['new_mpn']
            
            symbol_block = comp_data['block']
            
            if update['status'] == 'add':
                # Add MPN, Manufacturer, Datasheet, Description properties after Reference
                # Find the Reference property with proper paren matching
                ref_start = symbol_block.find('(property "Reference"')
                if ref_start != -1:
                    # Find the matching closing paren for this property
                    ref_end = _find_matching_paren(symbol_block, ref_start)
                    if ref_end != -1:
                        insert_point = ref_end + 1  # Insert after the closing )
                        
                        # Build properties to add
                        props_to_add = []
                        
                        # MPN
                        new_mpn = update['new_mpn']
                        if new_mpn:
                            props_to_add.append(f'\n\t\t\t(property "MPN" "{new_mpn}"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)')
                        
                        # Manufacturer
                        manufacturer = update.get('manufacturer', '')
                        if manufacturer:
                            props_to_add.append(f'\n\t\t\t(property "Manufacturer" "{manufacturer}"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)')
                        
                        # Datasheet - Only if we have it and it's not already there
                        datasheet = update.get('datasheet', '')
                        if datasheet and datasheet.lower() not in ['nan', 'none', '~']:
                            if '(property "Datasheet"' not in symbol_block:
                                props_to_add.append(f'\n\t\t\t(property "Datasheet" "{datasheet}"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)')
                        
                        # Description - Only if we have it and it's not already there  
                        description = update.get('description', '')
                        if description and description.lower() not in ['nan', 'none']:
                            if '(property "Description"' not in symbol_block:
                                # Escape quotes in description
                                description = description.replace('"', '\\"')
                                props_to_add.append(f'\n\t\t\t(property "Description" "{description}"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)')
                        
                        # Add all properties
                        if props_to_add:
                            new_block = symbol_block[:insert_point] + ''.join(props_to_add) + symbol_block[insert_point:]
                            text = text.replace(symbol_block, new_block)
                            updated_count += 1
            
            elif update['status'] == 'update':
                # Update existing MPN, Manufacturer, Datasheet, Description
                new_block = symbol_block
                
                # Update MPN value
                new_mpn = update['new_mpn']
                if new_mpn:
                    if '(property "MPN"' in new_block:
                        # Find and update the MPN value
                        mpn_start = new_block.find('(property "MPN" "')
                        if mpn_start != -1:
                            value_start = mpn_start + len('(property "MPN" "')
                            value_end = new_block.find('"', value_start)
                            if value_end != -1:
                                new_block = new_block[:value_start] + new_mpn + new_block[value_end:]
                    else:
                        # Add if not present - find Reference property end
                        ref_start = new_block.find('(property "Reference"')
                        if ref_start != -1:
                            ref_end = _find_matching_paren(new_block, ref_start)
                            if ref_end != -1:
                                insert_point = ref_end + 1
                                mpn_prop = f'\n\t\t\t(property "MPN" "{new_mpn}"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)'
                                new_block = new_block[:insert_point] + mpn_prop + new_block[insert_point:]
                
                # Update Manufacturer
                manufacturer = update.get('manufacturer', '')
                if manufacturer:
                    if '(property "Manufacturer"' in new_block:
                        # Find and update the Manufacturer value
                        mfr_start = new_block.find('(property "Manufacturer" "')
                        if mfr_start != -1:
                            value_start = mfr_start + len('(property "Manufacturer" "')
                            value_end = new_block.find('"', value_start)
                            if value_end != -1:
                                new_block = new_block[:value_start] + manufacturer + new_block[value_end:]
                    else:
                        # Add after MPN if it exists
                        mpn_start = new_block.find('(property "MPN"')
                        if mpn_start != -1:
                            mpn_end = _find_matching_paren(new_block, mpn_start)
                            if mpn_end != -1:
                                insert_point = mpn_end + 1
                                mfr_prop = f'\n\t\t\t(property "Manufacturer" "{manufacturer}"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)'
                                new_block = new_block[:insert_point] + mfr_prop + new_block[insert_point:]
                
                # Update Datasheet value (don't overwrite if already exists)
                datasheet = update.get('datasheet', '')
                if datasheet and datasheet.lower() not in ['nan', 'none', '~']:
                    if '(property "Datasheet"' in new_block:
                        # Check if current is just ~ or empty
                        ds_start = new_block.find('(property "Datasheet" "')
                        if ds_start != -1:
                            value_start = ds_start + len('(property "Datasheet" "')
                            value_end = new_block.find('"', value_start)
                            if value_end != -1:
                                current_value = new_block[value_start:value_end]
                                if current_value in ['~', '']:
                                    new_block = new_block[:value_start] + datasheet + new_block[value_end:]
                
                # Update Description value (don't overwrite if already exists)
                description = update.get('description', '')
                if description and description.lower() not in ['nan', 'none']:
                    description = description.replace('"', '\\"')
                    if '(property "Description"' in new_block:
                        # Only update if current is generic
                        desc_start = new_block.find('(property "Description" "')
                        if desc_start != -1:
                            value_start = desc_start + len('(property "Description" "')
                            value_end = new_block.find('"', value_start)
                            if value_end != -1:
                                current_value = new_block[value_start:value_end]
                                # Only overwrite if it's a generic description
                                if current_value in ['Unpolarized capacitor', 'Resistor', '']:
                                    new_block = new_block[:value_start] + description + new_block[value_end:]
                
                if new_block != symbol_block:
                    text = text.replace(symbol_block, new_block)
                    updated_count += 1
        
        # Write updated file
        sch_path.write_text(text, encoding='utf-8')
    
    return {
        'updated': updated_count,
        'files_modified': len(files_to_update)
    }


# ═══════════════════════════════════════════════════════════════════════════
# PASSIVES TAB v1 — Library-driven MPN/LCSC field assignment for R/C/L
# ═══════════════════════════════════════════════════════════════════════════
#
# Workflow:
#   1. Pick a schematic file (or .kicad_pro). Tool walks all hierarchical
#      sheets and gathers every R/C/L instance.
#   2. Table groups passives by (value_normalized, footprint_short) so
#      identical parts cluster — check N rows at once, apply one MPN/LCSC.
#   3. For Apply: either type MPN+LCSC manually, or pick a saved library
#      entry from the searchable dropdown. Library is filtered to entries
#      matching the (value, package) of the checked rows.
#   4. Save-to-library button captures the current MPN/LCSC plus user-typed
#      metadata (Voltage, Dielectric, etc.) into the global JSON library.
#   5. Library Manager… button opens a modal for browsing, editing, and
#      importing/exporting the library.
#
# File written to schematic, per part type:
#   R: MPN, LCSC, Tolerance, Power, Voltage, Type, Manufacturer
#   C: MPN, LCSC, Tolerance, Voltage, Dielectric, Type, Manufacturer
#   L: MPN, LCSC, Tolerance, Isat, Irms, DCR, SRF, Q, Core, Manufacturer
#
# Library file: ~/.kicad-library-tool/passives.json
# Schematic backup: <file>.kicad_sch.bak before any write.
# ═══════════════════════════════════════════════════════════════════════════

PASSIVES_LIB_DIR  = Path.home() / ".kicad-library-tool"
PASSIVES_LIB_FILE = PASSIVES_LIB_DIR / "passives.json"

# Per-type field map. Keys = (library_field_name, kicad_property_name).
# Order matters — properties are written in this order.
PASSIVE_FIELD_MAP: Dict[str, List[Tuple[str, str]]] = {
    'R': [('mpn','MPN'), ('lcsc','LCSC'), ('tolerance','Tolerance'),
          ('power','Power'), ('voltage','Voltage'), ('type','Type'),
          ('manufacturer','Manufacturer')],
    'C': [('mpn','MPN'), ('lcsc','LCSC'), ('tolerance','Tolerance'),
          ('voltage','Voltage'), ('dielectric','Dielectric'),
          ('type','Type'), ('manufacturer','Manufacturer')],
    'L': [('mpn','MPN'), ('lcsc','LCSC'), ('tolerance','Tolerance'),
          ('isat','Isat'), ('irms','Irms'), ('dcr','DCR'), ('srf','SRF'),
          ('q','Q'), ('core','Core'), ('manufacturer','Manufacturer')],
}

PASSIVE_TYPE_LABEL = {'R': 'Resistor', 'C': 'Capacitor', 'L': 'Inductor'}
PASSIVE_LIB_KEY    = {'R': 'resistors', 'C': 'capacitors', 'L': 'inductors'}

# Form-field layout per part type. Each entry is (key, label, hint).
# Used by both the single-entry dialog and the "Save All Complete" walkthrough.
PASSIVE_FORM_FIELDS: Dict[str, List[Tuple[str, str, str]]] = {
    'R': [('value','Value','e.g. 10k'),
          ('package','Package (full KiCad footprint)','e.g. Resistor_SMD:R_0603_1608Metric'),
          ('tolerance','Tolerance','e.g. 1%'),
          ('power','Power','e.g. 0.1W'),
          ('voltage','Voltage','e.g. 75V'),
          ('type','Type','Thick film / Thin film / Wirewound / Current sense'),
          ('manufacturer','Manufacturer','e.g. Yageo'),
          ('mpn','MPN','exact part number'),
          ('lcsc','LCSC','e.g. C25804'),
          ('datasheet','Datasheet URL',''),
          ('notes','Notes','optional')],
    'C': [('value','Value','e.g. 1uF'),
          ('package','Package (full KiCad footprint)','e.g. Capacitor_SMD:C_0603_1608Metric'),
          ('tolerance','Tolerance','e.g. 10%'),
          ('voltage','Voltage','e.g. 16V'),
          ('dielectric','Dielectric','C0G/NP0, X7R, X5R, X8R, Y5V, Tantalum, Polymer, Electrolytic'),
          ('type','Type','MLCC / Tantalum / Polymer / Aluminum Electrolytic / Film'),
          ('manufacturer','Manufacturer','e.g. Murata'),
          ('mpn','MPN','exact part number'),
          ('lcsc','LCSC','e.g. C28323'),
          ('datasheet','Datasheet URL',''),
          ('notes','Notes','optional')],
    'L': [('value','Value','e.g. 4.7uH'),
          ('package','Package (full KiCad footprint)',''),
          ('tolerance','Tolerance','e.g. 20%'),
          ('isat','Saturation Current (Isat)','e.g. 5.4A'),
          ('irms','RMS Current (Irms)','e.g. 4.6A'),
          ('dcr','DC Resistance (DCR)','e.g. 26mΩ'),
          ('srf','Self-Resonant Freq (SRF)','e.g. 50MHz'),
          ('q','Q Factor',''),
          ('core','Core Material','Ferrite / Iron powder / Air-core'),
          ('manufacturer','Manufacturer',''),
          ('mpn','MPN','exact part number'),
          ('lcsc','LCSC','e.g. C2845796'),
          ('datasheet','Datasheet URL',''),
          ('notes','Notes','optional')],
}

# Standard KiCad SMD footprint paths per part type. The combobox in the
# library-entry form drops down with these plus any extras already present
# in the user's library. Editable so users can type any custom name —
# custom names auto-populate the dropdown on next open.
COMMON_PASSIVE_PACKAGES: Dict[str, List[str]] = {
    'R': [
        'Resistor_SMD:R_01005_0402Metric',
        'Resistor_SMD:R_0201_0603Metric',
        'Resistor_SMD:R_0402_1005Metric',
        'Resistor_SMD:R_0603_1608Metric',
        'Resistor_SMD:R_0805_2012Metric',
        'Resistor_SMD:R_1206_3216Metric',
        'Resistor_SMD:R_1210_3225Metric',
        'Resistor_SMD:R_1218_3246Metric',
        'Resistor_SMD:R_2010_5025Metric',
        'Resistor_SMD:R_2512_6332Metric',
        # HandSolder variants (popular for prototyping)
        'Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder',
        'Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder',
        'Resistor_SMD:R_0805_2012Metric_Pad1.20x1.40mm_HandSolder',
        'Resistor_SMD:R_1206_3216Metric_Pad1.30x1.75mm_HandSolder',
        'Resistor_SMD:R_1210_3225Metric_Pad1.30x2.65mm_HandSolder',
    ],
    'C': [
        # Ceramic chip caps
        'Capacitor_SMD:C_01005_0402Metric',
        'Capacitor_SMD:C_0201_0603Metric',
        'Capacitor_SMD:C_0402_1005Metric',
        'Capacitor_SMD:C_0603_1608Metric',
        'Capacitor_SMD:C_0805_2012Metric',
        'Capacitor_SMD:C_1206_3216Metric',
        'Capacitor_SMD:C_1210_3225Metric',
        'Capacitor_SMD:C_1812_4532Metric',
        'Capacitor_SMD:C_1825_4564Metric',
        'Capacitor_SMD:C_2220_5650Metric',
        # HandSolder variants
        'Capacitor_SMD:C_0402_1005Metric_Pad0.74x0.62mm_HandSolder',
        'Capacitor_SMD:C_0603_1608Metric_Pad1.08x0.95mm_HandSolder',
        'Capacitor_SMD:C_0805_2012Metric_Pad1.18x1.45mm_HandSolder',
        'Capacitor_SMD:C_1206_3216Metric_Pad1.33x1.80mm_HandSolder',
        # Aluminum electrolytic SMD
        'Capacitor_SMD:CP_Elec_4x5.4',
        'Capacitor_SMD:CP_Elec_5x5.4',
        'Capacitor_SMD:CP_Elec_5x5.8',
        'Capacitor_SMD:CP_Elec_6.3x5.4',
        'Capacitor_SMD:CP_Elec_6.3x5.8',
        'Capacitor_SMD:CP_Elec_6.3x7.7',
        'Capacitor_SMD:CP_Elec_8x6.7',
        'Capacitor_SMD:CP_Elec_8x10.5',
        'Capacitor_SMD:CP_Elec_10x10.5',
        'Capacitor_SMD:CP_Elec_10x12.5',
        # Tantalum SMD (separate library)
        'Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A',
        'Capacitor_Tantalum_SMD:CP_EIA-3528-12_Kemet-B',
        'Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-T',
        'Capacitor_Tantalum_SMD:CP_EIA-6032-15_Kemet-C',
        'Capacitor_Tantalum_SMD:CP_EIA-6032-28_Kemet-D',
        'Capacitor_Tantalum_SMD:CP_EIA-7343-31_Kemet-D',
        'Capacitor_Tantalum_SMD:CP_EIA-7343-43_Kemet-E',
    ],
    'L': [
        # Inductor chip footprints
        'Inductor_SMD:L_01005_0402Metric',
        'Inductor_SMD:L_0201_0603Metric',
        'Inductor_SMD:L_0402_1005Metric',
        'Inductor_SMD:L_0603_1608Metric',
        'Inductor_SMD:L_0805_2012Metric',
        'Inductor_SMD:L_1008_2520Metric',
        'Inductor_SMD:L_1206_3216Metric',
        'Inductor_SMD:L_1210_3225Metric',
        'Inductor_SMD:L_1812_4532Metric',
        'Inductor_SMD:L_2010_5025Metric',
        'Inductor_SMD:L_2512_6332Metric',
        # Common shielded power inductor families (Coilcraft XAL series
        # is widely used). Format: L_Coilcraft_XAL<size>xx_<dim>.
        # Sizes documented in the KiCad library; pick the matching xx if
        # not auto-completed.
    ],
}


def _passives_known_packages(part_type: str, lib: dict) -> List[str]:
    """Return the dropdown list for the package field: standard KiCad SMD
    packages for the part type, plus any extras already used in the library
    (sorted alphabetically after the standard list)."""
    common = COMMON_PASSIVE_PACKAGES.get(part_type, [])
    bucket = PASSIVE_LIB_KEY.get(part_type, '')
    in_lib = set()
    for entry in lib.get(bucket, []):
        pkg = (entry.get('package') or '').strip()
        if pkg:
            in_lib.add(pkg)
    common_set = set(common)
    extras = sorted(in_lib - common_set)
    return common + extras


# ───── Unit normalization ─────────────────────────────────────────────────

# Old-style EE notation: "4k7" = 4.7k, "2R2" = 2.2 ohm, "1R0" = 1.0 ohm
_PASS_OLDSTYLE_RE = re.compile(
    r'^\s*(?P<int>\d+)(?P<mult>[RrkKMmuUμµnNpP])(?P<frac>\d+)\s*(?P<unit>[a-zA-ZΩ]*)\s*$'
)
# Standard form: optional sign, number, optional multiplier, optional unit
_PASS_STD_RE = re.compile(
    r'^\s*(?P<sign>[-+]?)'
    r'(?P<num>\d+(?:\.\d+)?|\.\d+)'
    r'(?P<mult>[pPnNuUμµmkKMgGtT])?'
    r'(?P<unit>[a-zA-ZΩ]*)\s*$'
)

def _passive_canon_mult(mult: str) -> str:
    """Canonicalize a multiplier prefix. m/M case is preserved (milli vs mega)."""
    if not mult: return ''
    if mult in ('u', 'U', 'μ', 'µ'): return 'u'
    if mult in ('k', 'K'): return 'k'
    if mult == 'm': return 'm'   # milli — stays lowercase
    if mult == 'M': return 'M'   # mega  — stays uppercase
    if mult in ('p', 'P'): return 'p'
    if mult in ('n', 'N'): return 'n'
    if mult in ('g', 'G'): return 'G'
    if mult in ('t', 'T'): return 'T'
    return mult

def _passive_canon_unit(rest: str) -> str:
    """Canonicalize a unit suffix. F/H/Ω uppercased; 'ohm/ohms' → Ω."""
    if not rest: return ''
    upper = rest.upper()
    if upper in ('OHM', 'OHMS'): return 'Ω'
    if upper in ('F', 'H', 'Ω'): return upper
    return rest

def normalize_passive_value(raw: str) -> str:
    """Return canonical form of a passive value:
      - 1uF == 1UF == 1uf == 1µF
      - 10k == 10K
      - mΩ != MΩ (case preserved for milli vs mega)
      - 4k7 → 4.7k, 2R2 → 2.2
      - 100nF stays 100nF
    Pass-through for non-numeric values (e.g. "DNP", a part-number value)."""
    if not raw: return raw
    s = raw.strip()
    m = _PASS_OLDSTYLE_RE.match(s)
    if m:
        intp, mult, frac = m.group('int'), m.group('mult'), m.group('frac')
        unit = m.group('unit') or ''
        if mult in ('R', 'r'):
            return f"{intp}.{frac}{_passive_canon_unit(unit)}"
        return f"{intp}.{frac}{_passive_canon_mult(mult)}{_passive_canon_unit(unit)}"
    m = _PASS_STD_RE.match(s)
    if not m:
        return s
    return (f"{m.group('sign') or ''}{m.group('num')}"
            f"{_passive_canon_mult(m.group('mult') or '')}"
            f"{_passive_canon_unit(m.group('unit') or '')}")


# ───── Footprint helpers ─────────────────────────────────────────────────

_PASS_FP_SIZE_RE = re.compile(r'_(\d{4})_')

def footprint_short(full_fp: str) -> str:
    """Extract '0603' style short package size from a full KiCad footprint id.
    Falls back to the lib-stripped name if no 4-digit size found."""
    if not full_fp: return ''
    m = _PASS_FP_SIZE_RE.search(full_fp)
    if m: return m.group(1)
    if ':' in full_fp:
        return full_fp.split(':', 1)[1]
    return full_fp


def _passive_type_from_ref(ref: str) -> Optional[str]:
    """Return 'R', 'C', or 'L' for properly-annotated passives, else None."""
    if not ref: return None
    m = re.match(r'^([RCL])(\d+)$', ref)
    return m.group(1) if m else None


# ───── Symbol-block property accessors ────────────────────────────────────

def _get_property_from_block(block: str, name: str) -> Optional[str]:
    """Extract a named property's value from a symbol block. None if absent."""
    pattern = f'(property "{name}" "'
    i = block.find(pattern)
    if i == -1:
        return None
    val_start = i + len(pattern)
    val_end = block.find('"', val_start)
    if val_end == -1:
        return None
    return block[val_start:val_end]


def _set_property_in_block(block: str, name: str, value: str) -> str:
    """Set a property by name in a symbol block. Updates the value in-place if
    the property already exists; otherwise inserts a new (property ...) block
    after the last existing property, preserving the surrounding indentation
    and line-ending style."""
    esc_value = value.replace('\\', '\\\\').replace('"', '\\"')

    pattern = f'(property "{name}" "'
    i = block.find(pattern)
    if i != -1:
        # Update existing — replace just the text between the value's quotes.
        val_start = i + len(pattern)
        val_end = block.find('"', val_start)
        if val_end == -1:
            return block
        return block[:val_start] + esc_value + block[val_end:]

    # Insert new property after the last existing one.
    last_prop_start = block.rfind('(property "')
    if last_prop_start == -1:
        return block  # malformed block — bail out without modifying
    last_prop_end = _find_matching_paren(block, last_prop_start)
    if last_prop_end == -1:
        return block

    # Detect indent (tabs/spaces) on the line of the last property.
    line_start = block.rfind('\n', 0, last_prop_start) + 1
    indent = block[line_start:last_prop_start]
    eol = '\r\n' if '\r\n' in block else '\n'

    new_prop = (
        f'{eol}{indent}(property "{name}" "{esc_value}"'
        f'{eol}{indent}\t(at 0 0 0)'
        f'{eol}{indent}\t(effects'
        f'{eol}{indent}\t\t(font'
        f'{eol}{indent}\t\t\t(size 1.27 1.27)'
        f'{eol}{indent}\t\t)'
        f'{eol}{indent}\t\t(hide yes)'
        f'{eol}{indent}\t)'
        f'{eol}{indent})'
    )
    insert_point = last_prop_end + 1
    return block[:insert_point] + new_prop + block[insert_point:]


# ───── Library load/save ──────────────────────────────────────────────────

def passives_library_load() -> dict:
    """Load the global passives library from disk. Returns an empty skeleton
    if the file doesn't exist or is invalid."""
    skeleton = {'version': 1, 'resistors': [], 'capacitors': [], 'inductors': []}
    if not PASSIVES_LIB_FILE.exists():
        return skeleton
    try:
        data = json.loads(PASSIVES_LIB_FILE.read_text(encoding='utf-8'))
        # Ensure all keys exist
        for k in ('resistors', 'capacitors', 'inductors'):
            data.setdefault(k, [])
        data.setdefault('version', 1)
        return data
    except (json.JSONDecodeError, OSError):
        return skeleton


def passives_library_save(lib: dict) -> None:
    """Save the library to disk, creating the dir if needed."""
    PASSIVES_LIB_DIR.mkdir(parents=True, exist_ok=True)
    PASSIVES_LIB_FILE.write_text(json.dumps(lib, indent=2, ensure_ascii=False),
                                 encoding='utf-8')


def passives_library_match(part_type: str, value_norm: str, package_short: str,
                            lib: dict) -> List[dict]:
    """Find library entries that match the given normalized value and short
    package code. Both must be non-empty for a match."""
    if not value_norm or not package_short:
        return []
    bucket_key = PASSIVE_LIB_KEY.get(part_type)
    if not bucket_key:
        return []
    matches = []
    for entry in lib.get(bucket_key, []):
        e_val = normalize_passive_value(entry.get('value', ''))
        e_pkg = footprint_short(entry.get('package', ''))
        if e_val == value_norm and e_pkg == package_short:
            matches.append(entry)
    return matches


def passives_library_next_id(lib: dict, part_type: str) -> str:
    """Generate a new unique id for a library entry."""
    bucket_key = PASSIVE_LIB_KEY[part_type]
    nums = []
    prefix = part_type.lower() + '_'
    for entry in lib.get(bucket_key, []):
        eid = entry.get('id', '')
        if eid.startswith(prefix):
            try:
                nums.append(int(eid[len(prefix):]))
            except ValueError:
                pass
    nxt = max(nums, default=0) + 1
    return f"{prefix}{nxt:04d}"


# ───── Schematic walker ──────────────────────────────────────────────────

def parse_passives_from_project(entry_file: Path) -> List[dict]:
    """Walk a project (or single sch file) and return all R/C/L instances.
    Each dict contains: ref, type ('R'/'C'/'L'), value, value_norm,
    footprint_full, footprint_short, mpn, lcsc, all_props (dict of all
    custom property values currently on the symbol), block, file (Path)."""
    sch_data = parse_kicad_project(entry_file)
    passives = []
    for file_path, file_data in sch_data.items():
        for comp in file_data['components']:
            pt = _passive_type_from_ref(comp['ref'])
            if pt is None:
                continue
            block = comp['block']
            all_props = {}
            # Capture every property currently set on the symbol so the Edit
            # dialog has full context. Cheap regex, only on a single block.
            for m in re.finditer(r'\(property "([^"]+)" "([^"]*)"', block):
                all_props[m.group(1)] = m.group(2)
            passives.append({
                'ref':             comp['ref'],
                'type':            pt,
                'value':           comp['value'],
                'value_norm':      normalize_passive_value(comp['value']),
                'footprint_full':  comp['footprint'],
                'footprint_short': footprint_short(comp['footprint']),
                'mpn':             all_props.get('MPN', ''),
                'lcsc':            all_props.get('LCSC', ''),
                'all_props':       all_props,
                'block':           block,
                'file':            file_path,
            })
    return passives


# ───── Apply updates to schematics ───────────────────────────────────────

def apply_passives_updates(updates: List[dict]) -> dict:
    """Apply a list of updates to schematic files.
    
    Each update dict requires:
      - 'passive': the passive dict (must have 'block' and 'file')
      - 'fields':  dict of {kicad_property_name: new_value}

    Updates are grouped by file. Each file is read once, all blocks for
    that file are updated, a .bak is taken, then the new text is written.

    Returns: {'updated': N_components, 'files_modified': N_files,
              'errors': [list of error strings]}.
    """
    by_file: Dict[str, List[dict]] = {}
    for u in updates:
        f = u['passive']['file']
        by_file.setdefault(f, []).append(u)

    errors = []
    updated_count = 0

    for file_path, ups in by_file.items():
        sch_path = Path(file_path)
        if not sch_path.exists():
            errors.append(f"File missing: {sch_path}")
            continue
        text = sch_path.read_text(encoding='utf-8')

        # Sanity-check: every block we're about to replace must still exist
        # in the file exactly once. If not, the file changed under us.
        for u in ups:
            blk = u['passive']['block']
            occ = text.count(blk)
            if occ != 1:
                errors.append(f"{u['passive']['ref']}: block not found "
                              f"({occ} occurrences) — file may have changed "
                              f"externally")

        if errors:
            continue

        # Apply each update
        for u in ups:
            orig_block = u['passive']['block']
            new_block = orig_block
            for prop_name, val in u['fields'].items():
                if val is None:
                    continue
                new_block = _set_property_in_block(new_block, prop_name, val)
            if new_block != orig_block:
                text = text.replace(orig_block, new_block, 1)
                updated_count += 1

        # Backup then write
        backup_path = sch_path.with_suffix('.kicad_sch.bak')
        try:
            shutil.copy2(sch_path, backup_path)
            sch_path.write_text(text, encoding='utf-8')
        except OSError as e:
            errors.append(f"Write failed for {sch_path}: {e}")

    return {
        'updated':        updated_count,
        'files_modified': len(by_file) - sum(1 for f in by_file
                                             if not (Path(f).exists())),
        'errors':         errors,
    }


# ═══════════════════════════════════════════════════════════════════════════
# PASSIVES TAB — UI methods (bound to App class below)
# ═══════════════════════════════════════════════════════════════════════════

def _create_passives_tab(self, parent):
    """Create the Passives tab: schematic scanner + library-driven assignment."""
    parent.grid_columnconfigure(0, weight=1)
    parent.grid_rowconfigure(2, weight=1)  # table row gets stretch

    # State
    self.passives_data = []          # list of passive dicts
    self.passives_library = passives_library_load()
    self.passives_checked = set()    # set of refs currently checked
    self.passives_filter_type = tk.StringVar(value="All")
    self.passives_filter_status = tk.StringVar(value="All")
    self.passives_filter_text = tk.StringVar()
    self.passives_sch_file = tk.StringVar()
    # Manual-entry vars
    self.passives_manual_mpn = tk.StringVar()
    self.passives_manual_lcsc = tk.StringVar()

    # ── Section 1: schematic file picker + scan ──
    file_frame = ttk.Labelframe(parent, text="1. Schematic", padding=12)
    file_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
    file_frame.grid_columnconfigure(1, weight=1)

    ttk.Label(file_frame, text="Schematic / Project:").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(file_frame, textvariable=self.passives_sch_file,
              state="readonly").grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    ttk.Button(file_frame, text="📁 Browse",
               command=self._passives_browse_schematic).grid(
        row=0, column=2, padx=4, pady=4)
    ttk.Button(file_frame, text="🔄 Scan", command=self._passives_scan,
               style="Accent.TButton").grid(row=0, column=3, padx=4, pady=4)

    self.passives_status = tk.StringVar(
        value="Pick a .kicad_sch or .kicad_pro file and click Scan.")
    ttk.Label(file_frame, textvariable=self.passives_status,
              foreground=COLORS['text_muted']).grid(
        row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

    # ── Section 2: filters ──
    filter_frame = ttk.Frame(parent)
    filter_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=4)

    ttk.Label(filter_frame, text="Type:").pack(side="left", padx=(0, 4))
    ttk.Combobox(filter_frame, textvariable=self.passives_filter_type,
                 values=["All", "Resistors", "Capacitors", "Inductors"],
                 state="readonly", width=12).pack(side="left", padx=4)
    self.passives_filter_type.trace_add(
        "write", lambda *a: self._passives_refresh_table())

    ttk.Label(filter_frame, text="Status:").pack(side="left", padx=(12, 4))
    ttk.Combobox(filter_frame, textvariable=self.passives_filter_status,
                 values=["All", "Missing MPN", "Missing LCSC",
                         "Missing MPN or LCSC", "Has MPN and LCSC",
                         "Missing Footprint"],
                 state="readonly", width=20).pack(side="left", padx=4)
    self.passives_filter_status.trace_add(
        "write", lambda *a: self._passives_refresh_table())

    ttk.Label(filter_frame, text="Filter:").pack(side="left", padx=(12, 4))
    filter_entry = ttk.Entry(filter_frame, textvariable=self.passives_filter_text,
                             width=24)
    filter_entry.pack(side="left", padx=4)
    self.passives_filter_text.trace_add(
        "write", lambda *a: self._passives_refresh_table())

    self.passives_count_var = tk.StringVar()
    ttk.Label(filter_frame, textvariable=self.passives_count_var,
              foreground=COLORS['text_muted']).pack(side="right", padx=8)

    # ── Section 3: components table + action panel ──
    body = ttk.Frame(parent)
    body.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
    body.grid_columnconfigure(0, weight=3)
    body.grid_columnconfigure(1, weight=2)
    body.grid_rowconfigure(0, weight=1)

    # ── Left pane: Treeview ──
    tree_frame = ttk.Labelframe(body, text="2. Passives", padding=8)
    tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    tree_frame.grid_columnconfigure(0, weight=1)
    tree_frame.grid_rowconfigure(0, weight=1)

    cols = ("chk", "ref", "type", "value", "package", "mpn", "lcsc")
    self.passives_tree = ttk.Treeview(tree_frame, columns=cols,
                                       show="headings", height=18,
                                       selectmode="extended")
    self.passives_tree.grid(row=0, column=0, sticky="nsew")

    vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                        command=self.passives_tree.yview)
    vsb.grid(row=0, column=1, sticky="ns")
    self.passives_tree.configure(yscrollcommand=vsb.set)

    self.passives_tree.heading("chk",     text="✓")
    self.passives_tree.heading("ref",     text="Ref")
    self.passives_tree.heading("type",    text="Type")
    self.passives_tree.heading("value",   text="Value")
    self.passives_tree.heading("package", text="Package")
    self.passives_tree.heading("mpn",     text="MPN")
    self.passives_tree.heading("lcsc",    text="LCSC")

    self.passives_tree.column("chk",     width=36,  anchor="center", stretch=False)
    self.passives_tree.column("ref",     width=70,  anchor="w",      stretch=False)
    self.passives_tree.column("type",    width=70,  anchor="w",      stretch=False)
    self.passives_tree.column("value",   width=110, anchor="w")
    self.passives_tree.column("package", width=110, anchor="w")
    self.passives_tree.column("mpn",     width=170, anchor="w")
    self.passives_tree.column("lcsc",    width=90,  anchor="w")

    # Tag colors: missing fields warning, has-value highlight
    self.passives_tree.tag_configure(
        "missing", background="#3a2a16", foreground=COLORS['warning'])
    self.passives_tree.tag_configure(
        "has_values", foreground=COLORS['success'])
    self.passives_tree.tag_configure(
        "checked", background="#0d2e4a")

    self.passives_tree.bind("<Button-1>", self._passives_on_click)
    self.passives_tree.bind("<Double-Button-1>", self._passives_on_double_click)

    # Below the tree: select-all / select-group buttons
    select_bar = ttk.Frame(tree_frame)
    select_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Button(select_bar, text="Check Visible",
               command=self._passives_check_visible).pack(side="left", padx=2)
    ttk.Button(select_bar, text="Uncheck All",
               command=self._passives_uncheck_all).pack(side="left", padx=2)
    ttk.Button(select_bar, text="Edit Row…",
               command=self._passives_edit_row_cmd).pack(side="left", padx=12)

    # ── Right pane: action panel ──
    action = ttk.Frame(body)
    action.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
    action.grid_columnconfigure(0, weight=1)

    # 3a: selection summary + warning
    sel_frame = ttk.Labelframe(action, text="3. Selection", padding=10)
    sel_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    sel_frame.grid_columnconfigure(0, weight=1)
    self.passives_selection_var = tk.StringVar(value="No rows checked.")
    ttk.Label(sel_frame, textvariable=self.passives_selection_var,
              wraplength=320, justify="left").grid(row=0, column=0, sticky="w")
    self.passives_warning_var = tk.StringVar()
    self.passives_warning_lbl = ttk.Label(sel_frame, textvariable=self.passives_warning_var,
                                            foreground=COLORS['warning'],
                                            wraplength=320, justify="left")
    self.passives_warning_lbl.grid(row=1, column=0, sticky="w", pady=(4, 0))

    # 3b: Library dropdown
    lib_frame = ttk.Labelframe(action, text="4. Apply from Library", padding=10)
    lib_frame.grid(row=1, column=0, sticky="ew", pady=6)
    lib_frame.grid_columnconfigure(0, weight=1)

    self.passives_lib_combo_var = tk.StringVar()
    self.passives_lib_combo = ttk.Combobox(lib_frame,
                                            textvariable=self.passives_lib_combo_var,
                                            state="readonly")
    self.passives_lib_combo.grid(row=0, column=0, sticky="ew", pady=(0, 4))

    btn_row = ttk.Frame(lib_frame)
    btn_row.grid(row=1, column=0, sticky="ew")
    ttk.Button(btn_row, text="Apply from Library",
               command=self._passives_apply_from_library,
               style="Accent.TButton").pack(side="left", padx=2)
    ttk.Button(btn_row, text="📚 Manage Library…",
               command=self._passives_open_library_manager).pack(side="left", padx=2)

    # 3c: Manual entry
    man_frame = ttk.Labelframe(action, text="5. Apply Manually", padding=10)
    man_frame.grid(row=2, column=0, sticky="ew", pady=6)
    man_frame.grid_columnconfigure(1, weight=1)

    ttk.Label(man_frame, text="MPN:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
    ttk.Entry(man_frame, textvariable=self.passives_manual_mpn).grid(
        row=0, column=1, sticky="ew", pady=2)
    ttk.Label(man_frame, text="LCSC:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=2)
    ttk.Entry(man_frame, textvariable=self.passives_manual_lcsc).grid(
        row=1, column=1, sticky="ew", pady=2)

    btn_row2 = ttk.Frame(man_frame)
    btn_row2.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Button(btn_row2, text="Apply Manual",
               command=self._passives_apply_manual,
               style="Accent.TButton").pack(side="left", padx=2)

    # 3d: Save to library
    save_frame = ttk.Labelframe(action, text="6. Save to Library", padding=10)
    save_frame.grid(row=3, column=0, sticky="ew", pady=6)
    save_frame.grid_columnconfigure(0, weight=1)

    ttk.Button(save_frame, text="💾 Save Selected to Library",
               command=self._passives_save_to_library).pack(
        fill="x", pady=2)
    ttk.Button(save_frame, text="📚 Save All Complete to Library…",
               command=self._passives_save_all_complete).pack(
        fill="x", pady=2)
    ttk.Label(save_frame,
              text="Bulk: walks every unique part with Value+Footprint+MPN "
                   "through a pre-filled form. Required = Value, Package, MPN.",
              foreground=COLORS['text_muted'],
              font=("Segoe UI", 8),
              wraplength=320, justify="left").pack(anchor="w", pady=(4, 0))


# ───── Tab actions ────────────────────────────────────────────────────────

def _passives_browse_schematic(self):
    fname = filedialog.askopenfilename(
        title="Select Schematic or Project",
        filetypes=[("KiCad Schematic", "*.kicad_sch"),
                   ("KiCad Project", "*.kicad_pro"),
                   ("All Files", "*.*")])
    if fname:
        self.passives_sch_file.set(fname)


def _passives_scan(self):
    """Walk the schematic and populate the table."""
    fname = self.passives_sch_file.get()
    if not fname:
        messagebox.showwarning("No Schematic", "Please select a schematic file first.")
        return
    p = Path(fname)
    if not p.exists():
        messagebox.showerror("Not Found", f"File does not exist:\n{p}")
        return
    try:
        self.passives_data = parse_passives_from_project(p)
    except Exception as e:
        messagebox.showerror("Parse Error", f"Failed to parse schematic:\n{e}")
        return

    # Sort: value (natural numeric), then footprint, then ref
    self.passives_data.sort(key=lambda x: (
        x['type'],
        _passive_sort_key(x['value_norm']),
        x['footprint_short'],
        _ref_sort_key(x['ref']),
    ))

    self.passives_checked.clear()
    n = len(self.passives_data)
    n_r = sum(1 for p in self.passives_data if p['type'] == 'R')
    n_c = sum(1 for p in self.passives_data if p['type'] == 'C')
    n_l = sum(1 for p in self.passives_data if p['type'] == 'L')
    self.passives_status.set(
        f"Found {n} passives  ·  {n_r} R, {n_c} C, {n_l} L  ·  {p.name}")
    self._passives_refresh_table()


def _passive_sort_key(value_norm: str):
    """Natural sort key: 1k < 10k < 100k < 1M. Falls back to text for non-numeric."""
    multipliers = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3,
                   '': 1, 'k': 1e3, 'M': 1e6, 'G': 1e9, 'T': 1e12}
    m = re.match(r'^[-+]?(\d+(?:\.\d+)?|\.\d+)([pnumkMGT]?)', value_norm or '')
    if m:
        try:
            num = float(m.group(1)) * multipliers.get(m.group(2), 1)
            return (0, num)
        except ValueError:
            pass
    return (1, value_norm or '')


def _ref_sort_key(ref: str):
    """Sort R10 < R100 < R20 the human way."""
    m = re.match(r'^([A-Za-z]+)(\d+)$', ref or '')
    if m:
        return (m.group(1), int(m.group(2)))
    return (ref or '', 0)


def _passives_refresh_table(self):
    """Repopulate the tree with current data, applying type/status/text filters."""
    tree = self.passives_tree
    for iid in tree.get_children():
        tree.delete(iid)

    type_filter = self.passives_filter_type.get()
    status_filter = self.passives_filter_status.get()
    text_filter = (self.passives_filter_text.get() or '').strip().lower()
    type_map = {'Resistors': 'R', 'Capacitors': 'C', 'Inductors': 'L'}
    type_letter = type_map.get(type_filter)

    visible = 0
    for p in self.passives_data:
        if type_letter and p['type'] != type_letter:
            continue
        # Status filter
        if status_filter == "Missing MPN" and p['mpn']:
            continue
        elif status_filter == "Missing LCSC" and p['lcsc']:
            continue
        elif status_filter == "Missing MPN or LCSC" and p['mpn'] and p['lcsc']:
            continue
        elif status_filter == "Has MPN and LCSC" and (not p['mpn'] or not p['lcsc']):
            continue
        elif status_filter == "Missing Footprint" and p['footprint_full']:
            continue
        if text_filter:
            hay = f"{p['ref']} {p['value']} {p['footprint_short']} " \
                  f"{p['mpn']} {p['lcsc']}".lower()
            if text_filter not in hay:
                continue

        # Determine row tags
        tags = []
        if not p['value'] or not p['footprint_full']:
            tags.append("missing")
        elif p['mpn'] or p['lcsc']:
            tags.append("has_values")
        if p['ref'] in self.passives_checked:
            tags.append("checked")

        chk = "☑" if p['ref'] in self.passives_checked else "☐"
        tree.insert("", "end", iid=p['ref'],
                    values=(chk, p['ref'], PASSIVE_TYPE_LABEL[p['type']],
                            p['value'], p['footprint_short'] or '— missing —',
                            p['mpn'], p['lcsc']),
                    tags=tags)
        visible += 1

    self.passives_count_var.set(
        f"{visible} shown · {len(self.passives_checked)} checked")
    self._passives_update_selection_panel()


def _passives_on_click(self, event):
    """Toggle row checkbox if user clicked the ✓ column."""
    region = self.passives_tree.identify("region", event.x, event.y)
    column = self.passives_tree.identify_column(event.x)
    iid    = self.passives_tree.identify_row(event.y)
    if region != "cell" or column != "#1" or not iid:
        return  # let normal selection happen
    self._passives_toggle_check(iid)
    return "break"


def _passives_toggle_check(self, ref: str):
    """Toggle the checked-state of a given ref, with heterogeneous-selection
    block (can't mix different value/package)."""
    if ref in self.passives_checked:
        self.passives_checked.remove(ref)
        self._passives_refresh_table()
        return

    # Adding: enforce same (type, value_norm, package_short) as already-checked
    target = next((p for p in self.passives_data if p['ref'] == ref), None)
    if target is None:
        return

    if self.passives_checked:
        existing = next((p for p in self.passives_data
                          if p['ref'] in self.passives_checked), None)
        if existing is not None:
            same = (existing['type'] == target['type']
                    and existing['value_norm'] == target['value_norm']
                    and existing['footprint_short'] == target['footprint_short'])
            if not same:
                messagebox.showwarning(
                    "Selection Mismatch",
                    f"Selection must share Value and Package.\n\n"
                    f"Already checked:  {existing['type']} "
                    f"{existing['value_norm']} / "
                    f"{existing['footprint_short'] or '—'}\n"
                    f"Tried to check:   {target['type']} "
                    f"{target['value_norm']} / "
                    f"{target['footprint_short'] or '—'}\n\n"
                    f"Uncheck the others first if you want to switch groups.")
                return

    self.passives_checked.add(ref)
    self._passives_refresh_table()


def _passives_check_visible(self):
    """Check every currently-visible row that is compatible with the current
    selection group (or whichever group the first visible row defines)."""
    tree = self.passives_tree
    visible_refs = list(tree.get_children())
    if not visible_refs:
        return

    # Determine the target group from currently checked, or the first visible.
    if self.passives_checked:
        anchor_ref = next(iter(self.passives_checked))
    else:
        anchor_ref = visible_refs[0]
    anchor = next((p for p in self.passives_data
                   if p['ref'] == anchor_ref), None)
    if not anchor:
        return

    skipped = 0
    for ref in visible_refs:
        p = next((q for q in self.passives_data if q['ref'] == ref), None)
        if not p:
            continue
        if (p['type'] == anchor['type']
                and p['value_norm'] == anchor['value_norm']
                and p['footprint_short'] == anchor['footprint_short']):
            self.passives_checked.add(ref)
        else:
            skipped += 1

    self._passives_refresh_table()
    if skipped:
        self.passives_status.set(
            f"Checked compatible visible rows · skipped {skipped} "
            f"with different value/package")


def _passives_uncheck_all(self):
    self.passives_checked.clear()
    self._passives_refresh_table()


def _passives_update_selection_panel(self):
    """Update the right-pane selection summary + library dropdown."""
    checked = [p for p in self.passives_data
               if p['ref'] in self.passives_checked]
    n = len(checked)

    # Warning state for missing value/package on any checked row
    self.passives_warning_var.set("")
    if n == 0:
        self.passives_selection_var.set("No rows checked.")
        self.passives_lib_combo['values'] = []
        self.passives_lib_combo.set('')
        return

    anchor = checked[0]
    refs_str = ', '.join(p['ref'] for p in checked[:8])
    if n > 8:
        refs_str += f", … ({n} total)"
    self.passives_selection_var.set(
        f"{n} checked  ·  {PASSIVE_TYPE_LABEL[anchor['type']]} "
        f"{anchor['value']}  /  {anchor['footprint_short'] or '— missing —'}\n"
        f"{refs_str}")

    missing_v = sum(1 for p in checked if not p['value'])
    missing_f = sum(1 for p in checked if not p['footprint_full'])
    warns = []
    if missing_v:
        warns.append(f"⚠ {missing_v} have no Value")
    if missing_f:
        warns.append(f"⚠ {missing_f} have no Footprint")
    if warns:
        warns.append("Apply will SET these fields.")
    self.passives_warning_var.set("\n".join(warns))

    # Populate library dropdown with matches
    matches = passives_library_match(
        anchor['type'], anchor['value_norm'],
        anchor['footprint_short'], self.passives_library)
    self._passives_lib_matches = matches  # cache for apply
    if matches:
        labels = [_passive_lib_label(e, anchor['type']) for e in matches]
        self.passives_lib_combo['values'] = labels
        self.passives_lib_combo.set(labels[0])
    else:
        self.passives_lib_combo['values'] = ["(no library matches)"]
        self.passives_lib_combo.set("(no library matches)")


def _passive_lib_label(entry: dict, part_type: str) -> str:
    """One-line label for the library dropdown."""
    val = entry.get('value', '?')
    pkg = footprint_short(entry.get('package', '?'))
    mpn = entry.get('mpn', '?')
    lcsc = entry.get('lcsc', '?')
    extras = []
    if part_type == 'C':
        if entry.get('voltage'):    extras.append(entry['voltage'])
        if entry.get('dielectric'): extras.append(entry['dielectric'])
        if entry.get('tolerance'):  extras.append(entry['tolerance'])
    elif part_type == 'R':
        if entry.get('tolerance'):  extras.append(entry['tolerance'])
        if entry.get('power'):      extras.append(entry['power'])
    elif part_type == 'L':
        if entry.get('isat'):       extras.append(f"Isat {entry['isat']}")
        if entry.get('dcr'):        extras.append(f"DCR {entry['dcr']}")
    extras_s = ' '.join(extras) if extras else '—'
    return f"{val} {pkg} · {extras_s}  →  {mpn} / {lcsc}"


def _passives_apply_manual(self):
    """Apply manually-typed MPN+LCSC to all checked rows."""
    mpn  = self.passives_manual_mpn.get().strip()
    lcsc = self.passives_manual_lcsc.get().strip()
    if not mpn and not lcsc:
        messagebox.showwarning("Nothing to Apply",
            "Enter MPN and/or LCSC before clicking Apply Manual.")
        return
    fields = {}
    if mpn:  fields['MPN']  = mpn
    if lcsc: fields['LCSC'] = lcsc
    self._passives_apply_fields_to_checked(fields)


def _passives_apply_from_library(self):
    """Apply the selected library entry's fields to all checked rows."""
    if not getattr(self, '_passives_lib_matches', None):
        messagebox.showwarning("No Library Match",
            "No library entries match the current selection.\n\n"
            "Use 'Save to Library' to add one, or apply manually.")
        return
    label = self.passives_lib_combo_var.get()
    sel = next((e for i, e in enumerate(self._passives_lib_matches)
                if _passive_lib_label(e, e.get('type', '')) == label
                or label == _passive_lib_label(
                    e, [p for p in self.passives_data
                        if p['ref'] in self.passives_checked][0]['type'])),
               None)
    # Simpler: index by combo selection position
    idx = self.passives_lib_combo.current()
    if idx < 0 or idx >= len(self._passives_lib_matches):
        return
    entry = self._passives_lib_matches[idx]

    # Determine which fields to write based on part type of selection
    checked = [p for p in self.passives_data
               if p['ref'] in self.passives_checked]
    if not checked:
        return
    pt = checked[0]['type']
    fields = {}
    for lib_key, kicad_name in PASSIVE_FIELD_MAP[pt]:
        v = entry.get(lib_key, '')
        if v:
            fields[kicad_name] = v
    # Also stamp Value+Footprint if any checked row is missing them
    if any(not p['value'] for p in checked) and entry.get('value'):
        fields['Value'] = entry['value']
    if any(not p['footprint_full'] for p in checked) and entry.get('package'):
        fields['Footprint'] = entry['package']
    if entry.get('datasheet'):
        fields['Datasheet'] = entry['datasheet']

    self._passives_apply_fields_to_checked(fields)


def _passives_apply_fields_to_checked(self, fields: dict):
    """Common apply path: confirm overwrites if any, then write to disk."""
    checked = [p for p in self.passives_data
               if p['ref'] in self.passives_checked]
    if not checked:
        messagebox.showwarning("No Selection",
                               "Check at least one row before applying.")
        return

    # Detect existing values for any field we're about to overwrite
    conflicts = []  # (ref, field_name, old_value, new_value)
    for p in checked:
        for fname, fval in fields.items():
            existing = p['all_props'].get(fname, '')
            if fname == 'Value':
                existing = p['value']
            elif fname == 'Footprint':
                existing = p['footprint_full']
            if existing and existing != fval:
                conflicts.append((p['ref'], fname, existing, fval))

    if conflicts:
        if not self._passives_confirm_overwrite(conflicts):
            return

    # Build update list and apply
    updates = [{'passive': p, 'fields': dict(fields)} for p in checked]
    result = apply_passives_updates(updates)

    if result['errors']:
        messagebox.showerror("Apply Failed",
            "Some updates could not be applied:\n\n"
            + "\n".join(result['errors'][:10]))
        return

    n_updated = result['updated']
    messagebox.showinfo("Applied",
        f"Updated {n_updated} component{'s' if n_updated != 1 else ''}.\n\n"
        f"Backup saved as <file>.kicad_sch.bak\n\n"
        f"Re-scanning to refresh table…")

    # Re-scan to pick up the new state
    self._passives_scan()


def _passives_confirm_overwrite(self, conflicts):
    """Show a single dialog listing all conflicts. Return True if user confirms."""
    dlg = tk.Toplevel(self)
    dlg.title("Existing Values — Confirm Overwrite")
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    dlg.grab_set()
    dlg.geometry("700x450")

    ttk.Label(dlg, text=f"⚠ {len(conflicts)} field(s) already have values "
                        f"that will be overwritten:",
              foreground=COLORS['warning']).pack(anchor="w", padx=12, pady=(12, 6))

    cols = ("ref", "field", "old", "new")
    tv = ttk.Treeview(dlg, columns=cols, show="headings", height=14)
    tv.heading("ref",   text="Ref");      tv.column("ref",   width=80)
    tv.heading("field", text="Field");    tv.column("field", width=120)
    tv.heading("old",   text="Current");  tv.column("old",   width=220)
    tv.heading("new",   text="New");      tv.column("new",   width=220)
    for c in conflicts:
        tv.insert("", "end", values=c)
    tv.pack(fill="both", expand=True, padx=12, pady=4)

    result = {'confirmed': False}
    def do_confirm():
        result['confirmed'] = True
        dlg.destroy()
    def do_cancel():
        dlg.destroy()

    btns = ttk.Frame(dlg); btns.pack(fill="x", padx=12, pady=12)
    ttk.Button(btns, text="Cancel", command=do_cancel).pack(side="right", padx=4)
    ttk.Button(btns, text="Overwrite All", command=do_confirm,
               style="Accent.TButton").pack(side="right", padx=4)

    self.wait_window(dlg)
    return result['confirmed']


def _passives_save_to_library(self):
    """Pop a form to save the currently-selected MPN/LCSC + extra metadata
    to the library, so it can be re-applied to future projects."""
    checked = [p for p in self.passives_data
               if p['ref'] in self.passives_checked]
    if not checked:
        messagebox.showwarning("No Selection",
            "Check one or more rows whose MPN/LCSC you want to save.")
        return
    anchor = checked[0]

    # Pre-fill from the currently typed manual MPN/LCSC, falling back to the
    # MPN/LCSC already on the symbol.
    init_mpn  = self.passives_manual_mpn.get().strip()  or anchor['mpn']
    init_lcsc = self.passives_manual_lcsc.get().strip() or anchor['lcsc']

    self._passives_show_library_entry_dialog(
        part_type=anchor['type'],
        initial={
            'value':    anchor['value_norm'],
            'package':  anchor['footprint_full'],
            'mpn':      init_mpn,
            'lcsc':     init_lcsc,
        },
        on_save=self._passives_persist_new_entry,
        title="Save to Library",
    )


def _passives_persist_new_entry(self, part_type: str, entry: dict):
    """Add a new entry to the library and save."""
    entry['id'] = passives_library_next_id(self.passives_library, part_type)
    bucket = PASSIVE_LIB_KEY[part_type]
    self.passives_library.setdefault(bucket, []).append(entry)
    passives_library_save(self.passives_library)
    self._passives_update_selection_panel()
    messagebox.showinfo("Saved",
        f"Saved to library: {entry.get('value','?')} "
        f"{footprint_short(entry.get('package','?'))} "
        f"→  {entry.get('mpn','?')} / {entry.get('lcsc','?')}")


def _passives_show_library_entry_dialog(self, part_type: str, initial: dict,
                                         on_save, title: str,
                                         existing_id: Optional[str] = None):
    """Form for adding or editing a library entry. Shared by 'Save to Library'
    and the Library Manager."""
    dlg = tk.Toplevel(self)
    dlg.title(title)
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    dlg.grab_set()
    dlg.geometry("520x600")

    ttk.Label(dlg, text=f"{PASSIVE_TYPE_LABEL[part_type]} library entry",
              font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 6))

    form = ttk.Frame(dlg); form.pack(fill="both", expand=True, padx=12, pady=4)
    form.grid_columnconfigure(1, weight=1)

    # Fields per part type. Each entry is (key, label, hint).
    fields = PASSIVE_FORM_FIELDS[part_type]
    vars_ = {}
    pkg_values = _passives_known_packages(part_type, self.passives_library)
    for r, (key, label, hint) in enumerate(fields):
        ttk.Label(form, text=label + ":").grid(row=r, column=0, sticky="e",
                                                 padx=(0, 6), pady=3)
        v = tk.StringVar(value=initial.get(key, ''))
        vars_[key] = v
        if key == 'package':
            ttk.Combobox(form, textvariable=v, values=pkg_values).grid(
                row=r, column=1, sticky="ew", pady=3)
        else:
            ttk.Entry(form, textvariable=v).grid(row=r, column=1, sticky="ew", pady=3)
        if hint:
            ttk.Label(form, text=hint, foreground=COLORS['text_muted'],
                      font=("Segoe UI", 8)).grid(row=r, column=2,
                                                  sticky="w", padx=(6, 0))

    btns = ttk.Frame(dlg); btns.pack(fill="x", padx=12, pady=10)

    def do_save():
        entry = {k: v.get().strip() for k, v in vars_.items()}
        if not entry.get('value') or not entry.get('package'):
            messagebox.showwarning("Missing Fields",
                "Value and Package are required.", parent=dlg)
            return
        if not entry.get('mpn') and not entry.get('lcsc'):
            messagebox.showwarning("Missing Fields",
                "At least one of MPN or LCSC must be provided.", parent=dlg)
            return
        if existing_id:
            entry['id'] = existing_id
        dlg.destroy()
        on_save(part_type, entry)

    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
    ttk.Button(btns, text="Save",   command=do_save,
               style="Accent.TButton").pack(side="right", padx=4)


# ───── Edit row (forcible field change) ──────────────────────────────────

def _passives_on_double_click(self, event):
    """Double-click a row → open the row-edit dialog."""
    iid = self.passives_tree.identify_row(event.y)
    if iid:
        self._passives_edit_row(iid)


def _passives_edit_row_cmd(self):
    """Edit Row… button — operate on the focused row (single-click selection)."""
    sel = self.passives_tree.focus()
    if not sel:
        messagebox.showinfo("No Row",
            "Click a row to focus it, then click Edit Row.")
        return
    self._passives_edit_row(sel)


def _passives_edit_row(self, ref: str):
    """Open the per-row edit dialog. Lets you change ANY property on a single
    symbol, including Value/Footprint. Two-click commit."""
    p = next((q for q in self.passives_data if q['ref'] == ref), None)
    if not p:
        return

    dlg = tk.Toplevel(self)
    dlg.title(f"Edit {p['ref']}")
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    dlg.grab_set()
    dlg.geometry("640x600")

    ttk.Label(dlg, text=f"Editing {p['ref']} "
                        f"({PASSIVE_TYPE_LABEL[p['type']]})",
              font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
    ttk.Label(dlg, text="Edit any field. Empty fields will be left untouched. "
                        "Filled fields will be applied. You'll be asked to "
                        "confirm changes before write.",
              foreground=COLORS['text_muted'],
              wraplength=600, justify="left").pack(anchor="w", padx=12, pady=(0, 8))

    # Show all current properties + the standard fields for the part type
    standard_keys = ['Value', 'Footprint'] + \
                    [k for _, k in PASSIVE_FIELD_MAP[p['type']]]
    seen = set()
    rows = []
    for k in standard_keys:
        if k in seen: continue
        seen.add(k)
        if k == 'Value':
            rows.append((k, p['value']))
        elif k == 'Footprint':
            rows.append((k, p['footprint_full']))
        else:
            rows.append((k, p['all_props'].get(k, '')))
    # Plus any extra non-standard properties already on the symbol
    for k, v in p['all_props'].items():
        if k not in seen and k not in ('Reference',):
            seen.add(k)
            rows.append((k, v))

    body = ttk.Frame(dlg); body.pack(fill="both", expand=True, padx=12, pady=4)
    body.grid_columnconfigure(1, weight=1)

    vars_ = {}
    for r, (k, v) in enumerate(rows):
        ttk.Label(body, text=k + ":").grid(row=r, column=0, sticky="e",
                                            padx=(0, 6), pady=3)
        sv = tk.StringVar(value=v)
        vars_[k] = sv
        ttk.Entry(body, textvariable=sv).grid(row=r, column=1, sticky="ew", pady=3)
        # Show current value beside it
        ttk.Label(body, text=f"current: {v!r}" if v else "(empty)",
                  foreground=COLORS['text_muted'],
                  font=("Segoe UI", 8)).grid(row=r, column=2,
                                              sticky="w", padx=(6, 0))

    btns = ttk.Frame(dlg); btns.pack(fill="x", padx=12, pady=12)

    def do_save():
        # Build diff: only fields that changed
        changes = []
        for k, sv in vars_.items():
            new_val = sv.get().strip()
            if k == 'Value':
                old_val = p['value']
            elif k == 'Footprint':
                old_val = p['footprint_full']
            else:
                old_val = p['all_props'].get(k, '')
            if new_val != old_val:
                changes.append((k, old_val, new_val))
        if not changes:
            messagebox.showinfo("No Changes", "Nothing to apply.", parent=dlg)
            return
        # Confirm
        msg_lines = [f"Apply these {len(changes)} change(s) to {p['ref']}?", ""]
        for k, old, new in changes:
            msg_lines.append(f"  {k}: {old!r} → {new!r}")
        if not messagebox.askyesno("Confirm Edit", "\n".join(msg_lines),
                                    parent=dlg):
            return

        fields = {k: new for (k, _old, new) in changes}
        result = apply_passives_updates(
            [{'passive': p, 'fields': fields}])
        if result['errors']:
            messagebox.showerror("Edit Failed",
                "\n".join(result['errors']), parent=dlg)
            return
        dlg.destroy()
        self._passives_scan()  # refresh

    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
    ttk.Button(btns, text="Save Changes…", command=do_save,
               style="Accent.TButton").pack(side="right", padx=4)


# ───── Library Manager ───────────────────────────────────────────────────

def _passives_open_library_manager(self):
    """Modal dialog for browsing/editing/removing/import/export library entries."""
    dlg = tk.Toplevel(self)
    dlg.title("Passives Library Manager")
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    dlg.grab_set()
    dlg.geometry("1000x650")

    # Tabs by part type
    nb = ttk.Notebook(dlg)
    nb.pack(fill="both", expand=True, padx=12, pady=(12, 6))

    type_tabs = {}
    for pt in ('R', 'C', 'L'):
        f = ttk.Frame(nb); nb.add(f, text=f"  {PASSIVE_TYPE_LABEL[pt]}s  ")
        type_tabs[pt] = f

    # Build each tab
    trees = {}
    for pt, frame in type_tabs.items():
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        cols = ('value', 'package', 'extras', 'mpn', 'lcsc', 'mfg')
        tv = ttk.Treeview(frame, columns=cols, show="headings", height=18)
        tv.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        tv.configure(yscrollcommand=vsb.set)
        for c, h, w in [('value','Value',100), ('package','Package',180),
                        ('extras','Specs',260), ('mpn','MPN',180),
                        ('lcsc','LCSC',100), ('mfg','Mfg',120)]:
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="w")
        trees[pt] = tv

    def refresh():
        for pt, tv in trees.items():
            for iid in tv.get_children():
                tv.delete(iid)
            for entry in self.passives_library.get(PASSIVE_LIB_KEY[pt], []):
                extras = []
                if pt == 'C':
                    if entry.get('voltage'):    extras.append(entry['voltage'])
                    if entry.get('dielectric'): extras.append(entry['dielectric'])
                    if entry.get('tolerance'):  extras.append(entry['tolerance'])
                elif pt == 'R':
                    if entry.get('tolerance'):  extras.append(entry['tolerance'])
                    if entry.get('power'):      extras.append(entry['power'])
                    if entry.get('voltage'):    extras.append(entry['voltage'])
                    if entry.get('type'):       extras.append(entry['type'])
                elif pt == 'L':
                    if entry.get('isat'): extras.append(f"Isat {entry['isat']}")
                    if entry.get('dcr'):  extras.append(f"DCR {entry['dcr']}")
                    if entry.get('core'): extras.append(entry['core'])
                tv.insert("", "end", iid=entry['id'],
                          values=(entry.get('value',''),
                                  footprint_short(entry.get('package','')),
                                  ' '.join(extras),
                                  entry.get('mpn',''),
                                  entry.get('lcsc',''),
                                  entry.get('manufacturer','')))
    refresh()

    # Bottom buttons
    btns = ttk.Frame(dlg); btns.pack(fill="x", padx=12, pady=8)

    def current_pt():
        idx = nb.index(nb.select())
        return ('R', 'C', 'L')[idx]

    def add_entry():
        pt = current_pt()
        self._passives_show_library_entry_dialog(
            part_type=pt, initial={},
            on_save=lambda part_type, entry: (
                self._passives_persist_new_entry(part_type, entry), refresh()),
            title=f"Add {PASSIVE_TYPE_LABEL[pt]} Entry")

    def edit_entry():
        pt = current_pt()
        sel = trees[pt].focus()
        if not sel:
            messagebox.showinfo("No Selection", "Pick an entry to edit.", parent=dlg)
            return
        bucket = PASSIVE_LIB_KEY[pt]
        entry = next((e for e in self.passives_library.get(bucket, [])
                      if e.get('id') == sel), None)
        if not entry: return

        def do_update(part_type, new_entry):
            # Replace entry in-place
            new_entry['id'] = sel
            for i, e in enumerate(self.passives_library[bucket]):
                if e.get('id') == sel:
                    self.passives_library[bucket][i] = new_entry
                    break
            passives_library_save(self.passives_library)
            refresh()
            self._passives_update_selection_panel()

        self._passives_show_library_entry_dialog(
            part_type=pt, initial=entry, on_save=do_update,
            title=f"Edit {PASSIVE_TYPE_LABEL[pt]} Entry",
            existing_id=sel)

    def remove_entry():
        pt = current_pt()
        sel = trees[pt].focus()
        if not sel:
            messagebox.showinfo("No Selection", "Pick an entry to remove.", parent=dlg)
            return
        bucket = PASSIVE_LIB_KEY[pt]
        entry = next((e for e in self.passives_library.get(bucket, [])
                      if e.get('id') == sel), None)
        if not entry: return
        if not messagebox.askyesno("Remove Entry",
                f"Remove {entry.get('value','?')} "
                f"({entry.get('mpn','?')}) from library?", parent=dlg):
            return
        self.passives_library[bucket] = [
            e for e in self.passives_library[bucket] if e.get('id') != sel]
        passives_library_save(self.passives_library)
        refresh()
        self._passives_update_selection_panel()

    def export_lib():
        fname = filedialog.asksaveasfilename(
            title="Export Library", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All Files", "*.*")],
            initialfile="passives_library.json", parent=dlg)
        if not fname:
            return
        try:
            Path(fname).write_text(
                json.dumps(self.passives_library, indent=2, ensure_ascii=False),
                encoding='utf-8')
            n = sum(len(self.passives_library.get(b, []))
                    for b in ('resistors', 'capacitors', 'inductors'))
            messagebox.showinfo("Exported",
                f"Exported {n} entries to:\n{fname}", parent=dlg)
        except OSError as e:
            messagebox.showerror("Export Failed", str(e), parent=dlg)

    def import_lib():
        fname = filedialog.askopenfilename(
            title="Import Library", filetypes=[("JSON","*.json"),("All Files","*.*")],
            parent=dlg)
        if not fname:
            return
        try:
            data = json.loads(Path(fname).read_text(encoding='utf-8'))
        except Exception as e:
            messagebox.showerror("Import Failed",
                f"Could not parse JSON:\n{e}", parent=dlg)
            return
        choice = messagebox.askyesnocancel("Import Mode",
            "Yes  = Merge into current library (skip duplicate ids)\n"
            "No   = Replace current library\n"
            "Cancel = Abort", parent=dlg)
        if choice is None:
            return
        if not choice:
            self.passives_library = {'version': 1, 'resistors': [],
                                       'capacitors': [], 'inductors': []}
        for bucket in ('resistors', 'capacitors', 'inductors'):
            existing_ids = {e.get('id') for e in
                            self.passives_library.get(bucket, [])}
            for e in data.get(bucket, []):
                if e.get('id') not in existing_ids:
                    self.passives_library.setdefault(bucket, []).append(e)
        passives_library_save(self.passives_library)
        refresh()
        self._passives_update_selection_panel()

    ttk.Button(btns, text="➕ Add",     command=add_entry).pack(side="left", padx=2)
    ttk.Button(btns, text="✏ Edit",    command=edit_entry).pack(side="left", padx=2)
    ttk.Button(btns, text="🗑 Remove",  command=remove_entry).pack(side="left", padx=2)
    ttk.Button(btns, text="📤 Export…", command=export_lib).pack(side="left", padx=12)
    ttk.Button(btns, text="📥 Import…", command=import_lib).pack(side="left", padx=2)
    ttk.Label(btns, text=f"  Library file: {PASSIVES_LIB_FILE}",
              foreground=COLORS['text_muted']).pack(side="left", padx=12)
    ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="right", padx=2)


# ─── Save All Complete to Library — bulk walkthrough ─────────────────────

def _passives_save_all_complete(self):
    """Walk every unique (type, value_norm, package_short, MPN) in the scan
    that has Value+Footprint+MPN populated. Dedup against the existing
    library. Walk the remainder through a pre-filled form."""
    # Build set of keys already in library
    seen_in_lib = set()
    for pt, bucket_name in PASSIVE_LIB_KEY.items():
        for entry in self.passives_library.get(bucket_name, []):
            seen_in_lib.add((
                pt,
                normalize_passive_value(entry.get('value', '')),
                footprint_short(entry.get('package', '')),
                entry.get('mpn', ''),
            ))

    # Build queue from scan, deduped against library and against itself
    seen_in_queue = set()
    queue = []
    for p in self.passives_data:
        if not p['value'] or not p['footprint_full'] or not p['mpn']:
            continue
        key = (p['type'], p['value_norm'], p['footprint_short'], p['mpn'])
        if key in seen_in_lib or key in seen_in_queue:
            continue
        seen_in_queue.add(key)
        queue.append(p)

    if not queue:
        # Count what got filtered for a helpful message
        total_complete = sum(1 for p in self.passives_data
                              if p['value'] and p['footprint_full'] and p['mpn'])
        messagebox.showinfo("Nothing to Save",
            f"No new entries to save.\n\n"
            f"Components with Value+Footprint+MPN: {total_complete}\n"
            f"Unique (type, value, package, MPN) groups already in library: "
            f"all of them.\n\n"
            f"To re-edit an existing library entry, use "
            f"📚 Manage Library… and click Edit.")
        return

    self._passives_run_save_queue(queue)


def _passives_run_save_queue(self, queue):
    """Show a sequential walkthrough dialog. One pre-filled form per queue
    item with Save & Next / Skip / Save All Remaining (minimal) / Cancel."""
    state = {'index': 0, 'queue': queue}

    dlg = tk.Toplevel(self)
    dlg.title("Save All Complete — Library Walkthrough")
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    dlg.grab_set()
    dlg.geometry("620x680")

    # Header — progress label
    header = ttk.Frame(dlg)
    header.pack(fill="x", padx=12, pady=(12, 6))
    progress_var = tk.StringVar()
    ttk.Label(header, textvariable=progress_var,
              font=("Segoe UI", 11, "bold")).pack(side="left")

    # Body — cleared and rebuilt for each item
    body = ttk.Frame(dlg)
    body.pack(fill="both", expand=True, padx=12, pady=4)

    # Footer button row
    btns = ttk.Frame(dlg); btns.pack(fill="x", padx=12, pady=10)

    form_state = {'vars_': {}, 'part_type': None, 'sample': None}

    def make_entry_from_passive(p, vars_=None):
        """Build a library-entry dict. If vars_ given, read from form;
        otherwise build minimal entry directly from the passive."""
        if vars_ is not None:
            entry = {k: v.get().strip() for k, v in vars_.items()}
        else:
            entry = {
                'value':        p['value_norm'],
                'package':      p['footprint_full'],
                'mpn':          p['mpn'],
                'lcsc':         p['lcsc'],
                'manufacturer': p['all_props'].get('Manufacturer', ''),
            }
        return entry

    def persist_entry(p, entry):
        """Append a validated entry to the library (no file save yet)."""
        entry['id'] = passives_library_next_id(self.passives_library, p['type'])
        bucket_name = PASSIVE_LIB_KEY[p['type']]
        self.passives_library.setdefault(bucket_name, []).append(entry)

    def end_of_queue():
        passives_library_save(self.passives_library)
        dlg.destroy()
        total = sum(len(self.passives_library.get(b, []))
                     for b in ('resistors', 'capacitors', 'inductors'))
        messagebox.showinfo("Walkthrough Complete",
            f"Library now has {total} total entries.")
        self._passives_update_selection_panel()

    def render_current():
        # Clear body
        for child in body.winfo_children():
            child.destroy()
        body.grid_columnconfigure(1, weight=1)

        idx = state['index']
        if idx >= len(state['queue']):
            end_of_queue()
            return

        p = state['queue'][idx]
        form_state['part_type'] = p['type']
        form_state['sample'] = p
        progress_var.set(
            f"{idx + 1} of {len(state['queue'])}  ·  "
            f"{PASSIVE_TYPE_LABEL[p['type']]} {p['value']} / "
            f"{p['footprint_short']}")

        # Sub-header: which schematic components this entry will represent
        matching_refs = [q['ref'] for q in self.passives_data
                         if q['type'] == p['type']
                         and q['value_norm'] == p['value_norm']
                         and q['footprint_short'] == p['footprint_short']
                         and q['mpn'] == p['mpn']]
        refs_str = ', '.join(matching_refs[:12])
        if len(matching_refs) > 12:
            refs_str += f", … ({len(matching_refs)} total)"
        ttk.Label(body, text=f"Schematic refs: {refs_str}",
                  foreground=COLORS['text_muted'],
                  wraplength=560, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        # Pre-fill from schematic; required = Value/Package/MPN
        initial = {
            'value':        p['value_norm'],
            'package':      p['footprint_full'],
            'mpn':          p['mpn'],
            'lcsc':         p['lcsc'],
            'manufacturer': p['all_props'].get('Manufacturer', ''),
        }
        # Seed any spec fields already on the schematic
        for kicad_name in ('Tolerance', 'Voltage', 'Dielectric', 'Type',
                           'Power', 'Isat', 'Irms', 'DCR', 'SRF', 'Q', 'Core',
                           'Datasheet'):
            v = p['all_props'].get(kicad_name, '')
            if v:
                initial[kicad_name.lower()] = v

        fields = PASSIVE_FORM_FIELDS[p['type']]
        vars_ = {}
        REQUIRED = {'value', 'package', 'mpn'}
        pkg_values = _passives_known_packages(p['type'], self.passives_library)
        for r, (key, label, hint) in enumerate(fields, start=1):
            required_marker = " *" if key in REQUIRED else ""
            ttk.Label(body, text=label + required_marker + ":").grid(
                row=r, column=0, sticky="e", padx=(0, 6), pady=3)
            sv = tk.StringVar(value=initial.get(key, ''))
            vars_[key] = sv
            if key == 'package':
                ttk.Combobox(body, textvariable=sv, values=pkg_values).grid(
                    row=r, column=1, sticky="ew", pady=3)
            else:
                ttk.Entry(body, textvariable=sv).grid(
                    row=r, column=1, sticky="ew", pady=3)
            if hint:
                ttk.Label(body, text=hint, foreground=COLORS['text_muted'],
                          font=("Segoe UI", 8)).grid(
                    row=r, column=2, sticky="w", padx=(6, 0))

        # Required field note at bottom of form
        ttk.Label(body, text="*  required",
                  foreground=COLORS['text_muted'],
                  font=("Segoe UI", 8)).grid(
            row=len(fields) + 1, column=0, columnspan=3,
            sticky="w", pady=(6, 0))

        form_state['vars_'] = vars_

    def do_save_and_next():
        p = form_state['sample']
        vars_ = form_state['vars_']
        entry = make_entry_from_passive(p, vars_)
        if not entry.get('value') or not entry.get('package') \
                or not entry.get('mpn'):
            messagebox.showwarning("Missing Required Fields",
                "Value, Package, and MPN are required.\n\n"
                "Edit the fields, click Skip, or click Cancel.",
                parent=dlg)
            return
        persist_entry(p, entry)
        passives_library_save(self.passives_library)
        state['index'] += 1
        render_current()

    def do_skip():
        state['index'] += 1
        render_current()

    def do_save_all_remaining():
        remaining = len(state['queue']) - state['index']
        if not messagebox.askyesno("Save All Remaining",
            f"Save the remaining {remaining} entries with only Value, "
            f"Package, MPN, LCSC, and Manufacturer? Spec fields (Tolerance, "
            f"Voltage, etc.) will be left blank. You can fill them in "
            f"later via 📚 Manage Library…", parent=dlg):
            return
        for i in range(state['index'], len(state['queue'])):
            p = state['queue'][i]
            entry = make_entry_from_passive(p, vars_=None)
            persist_entry(p, entry)
        state['index'] = len(state['queue'])
        render_current()  # triggers end-of-queue path

    def do_cancel():
        dlg.destroy()
        # No file write needed — saves were committed incrementally.
        self._passives_update_selection_panel()

    ttk.Button(btns, text="Cancel", command=do_cancel).pack(side="left", padx=2)
    ttk.Button(btns, text="Save All Remaining (minimal)",
               command=do_save_all_remaining).pack(side="left", padx=12)
    ttk.Button(btns, text="Skip", command=do_skip).pack(side="right", padx=2)
    ttk.Button(btns, text="Save & Next", command=do_save_and_next,
               style="Accent.TButton").pack(side="right", padx=2)

    render_current()


# ─── Bind passives methods to App class ────────────────────────────────────
App._create_passives_tab               = _create_passives_tab
App._passives_browse_schematic         = _passives_browse_schematic
App._passives_scan                     = _passives_scan
App._passives_refresh_table            = _passives_refresh_table
App._passives_on_click                 = _passives_on_click
App._passives_toggle_check             = _passives_toggle_check
App._passives_check_visible            = _passives_check_visible
App._passives_uncheck_all              = _passives_uncheck_all
App._passives_update_selection_panel   = _passives_update_selection_panel
App._passives_apply_manual             = _passives_apply_manual
App._passives_apply_from_library       = _passives_apply_from_library
App._passives_apply_fields_to_checked  = _passives_apply_fields_to_checked
App._passives_confirm_overwrite        = _passives_confirm_overwrite
App._passives_save_to_library          = _passives_save_to_library
App._passives_persist_new_entry        = _passives_persist_new_entry
App._passives_show_library_entry_dialog = _passives_show_library_entry_dialog
App._passives_on_double_click          = _passives_on_double_click
App._passives_edit_row_cmd             = _passives_edit_row_cmd
App._passives_edit_row                 = _passives_edit_row
App._passives_open_library_manager     = _passives_open_library_manager
App._passives_save_all_complete        = _passives_save_all_complete
App._passives_run_save_queue           = _passives_run_save_queue


# ═══════════════════════════════════════════════════════════════════════════
# MULTI-PART CPL — one schematic symbol, several placed parts
# ═══════════════════════════════════════════════════════════════════════════
#
# Runs after the JLCPCB plugin (Bouni's kicad-jlcpcb-tools) has written
# jlcpcb/production_files/{BOM,CPL}-<board>.csv. For each configured parent
# footprint (e.g. a CM5 "Module1"), it drops the parent row and adds one row
# per sub-part (e.g. the two 100-pin B2B connectors J6/J7), placed from the
# parent's own pads in the .kicad_pcb.
#
# Positions follow the plugin's fabrication.py exactly: centre of the merged
# pad bounding boxes, minus the aux origin, Y negated; rotation is the
# footprint angle, mirrored (180 - a) on the bottom, plus a correction.
#
# Sub-part modes:
#   pads    centre of the listed pads; xy_offset_mm is a nudge on top
#   offset  xy_offset_mm from the parent origin, no pads needed
# Both offsets are in the parent's own frame (like footprint-editor
# coordinates), so they turn with the parent.
#
# Config: <project>.multipart.json next to the .kicad_pro
# User presets: ~/.kicad-library-tool/multipart_presets.json
# CSV backup: <file>.csv.bak before any write.
# ═══════════════════════════════════════════════════════════════════════════

MULTIPART_CONFIG_VERSION = 1
CM5_PRESET = "Raspberry Pi CM5 (2× 100-pin B2B)"

# Pad ranges match the CM5 datasheet numbering (101-200 is the second
# connector). expect_local_mm is the measured connector centre in the CM5
# footprint's frame; it is only a cross-check, the position comes from the pads.
# Part: Amphenol 10164227-1001A1RLF (G-Switch GT-B0403FSV14-100B1101, C42376244,
# is a pin-compatible alternative). Rotation is as ordered on M-050 Rev-4
# (connectors at 0 with the module at 90, with the G-Switch part); check it
# in JLC's placement preview, since each part has its own JLC footprint.
MULTIPART_PRESETS: Dict[str, dict] = {
    CM5_PRESET: {
        "comment": "10164227-1001A1RLF",
        "footprint": "CONN-SMD_100P-P0.40_10164227-1001A1RLF",
        "lcsc": "C6782225",
        "rotation_offset": -90,
        "subparts": [
            {"ref": "J6", "pads": "101-200", "expect_local_mm": [33.5, -21.5]},
            {"ref": "J7", "pads": "1-100", "expect_local_mm": [-0.5, -21.5]},
        ],
    },
}

_MULTIPART_PRESET_TOLERANCE_MM = 0.1
_SUBPART_DEFAULTS = {"mode": "pads", "pads": "", "comment": "", "footprint": "", "lcsc": "",
                     "rotation_offset": 0, "xy_offset_mm": [0, 0]}

# A quoted string (with escapes) or a single paren
_SEXP_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[()]')
_NUM = r'(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)'
_AT_RE = re.compile(r'\(at\s+' + _NUM + r'\s+' + _NUM + r'(?:\s+' + _NUM + r')?')
_SIZE_RE = re.compile(r'\(size\s+' + _NUM + r'\s+' + _NUM)
_XY_RE = re.compile(r'\((start|end)\s+' + _NUM + r'\s+' + _NUM)


def _sexp_unquote(s: str) -> str:
    return re.sub(r'\\(.)', r'\1', s[1:-1])


def _sexp_block_end(text: str, start: int) -> int:
    """Index of the ')' closing the '(' at start, skipping quoted strings
    (unlike _find_matching_paren). Returns -1 if unbalanced."""
    depth = 0
    for m in _SEXP_TOKEN_RE.finditer(text, start):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
            if depth == 0:
                return m.start()
    return -1


def _sexp_children(block: str, head: str) -> List[str]:
    """Direct child blocks of block (which starts with '(') named head."""
    out = []
    head_re = re.compile(r'\(' + re.escape(head) + r'[\s)]')
    depth = 0
    pos = block.find("(")
    while True:
        m = _SEXP_TOKEN_RE.search(block, pos)
        if not m:
            break
        tok = m.group(0)
        if tok == "(":
            if depth == 1 and head_re.match(block, m.start()):
                end = _sexp_block_end(block, m.start())
                if end < 0:
                    raise ValueError(f"unbalanced ({head} ...) block")
                out.append(block[m.start():end + 1])
                pos = end + 1
                continue
            depth += 1
        elif tok == ")":
            depth -= 1
        pos = m.end()
    return out


def parse_pad_spec(spec: str) -> Set[str]:
    """'1-100' / '1-50,52,EP' -> set of pad numbers. Ranges are numeric only."""
    pads: Set[str] = set()
    if not spec.strip():
        return pads
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty entry in pad list {spec!r}")
        if "-" in part:
            bits = part.split("-")
            if len(bits) != 2 or not all(b.strip().isdigit() for b in bits):
                raise ValueError(f"bad pad range {part!r} (ranges are numeric, e.g. 1-100)")
            lo, hi = int(bits[0]), int(bits[1])
            if lo > hi:
                raise ValueError(f"reversed pad range {part!r}")
            pads.update(str(n) for n in range(lo, hi + 1))
        else:
            pads.add(part)
    return pads


def _parse_pcb_footprint_block(block: str) -> dict:
    lib = re.match(r'\(footprint\s+("(?:\\.|[^"\\])*")', block)
    at = None
    for child in _sexp_children(block, "at"):
        at = _AT_RE.match(child)
        break
    layer = ""
    for child in _sexp_children(block, "layer"):
        m = re.match(r'\(layer\s+("(?:\\.|[^"\\])*")', child)
        layer = _sexp_unquote(m.group(1)) if m else ""
        break
    ref = ""
    for child in _sexp_children(block, "property"):
        m = re.match(r'\(property\s+"Reference"\s+("(?:\\.|[^"\\])*")', child)
        if m:
            ref = _sexp_unquote(m.group(1))
            break
    if not ref:  # KiCad 5 style
        for child in _sexp_children(block, "fp_text"):
            m = re.match(r'\(fp_text\s+reference\s+("(?:\\.|[^"\\])*"|\S+)', child)
            if m:
                ref = _sexp_unquote(m.group(1)) if m.group(1).startswith('"') else m.group(1)
                break
    pads = []
    for child in _sexp_children(block, "pad"):
        m = re.match(r'\(pad\s+("(?:\\.|[^"\\])*"|\S+)\s+(\S+)\s+(\S+)', child)
        pat, size = _AT_RE.search(child), _SIZE_RE.search(child)
        if not (m and pat and size):
            continue
        num = m.group(1)
        pads.append({
            "number": _sexp_unquote(num) if num.startswith('"') else num,
            "type": m.group(2), "shape": m.group(3),
            "x": float(pat.group(1)), "y": float(pat.group(2)),
            # Pad angles in the file are absolute (they include the footprint's)
            "angle": float(pat.group(3) or 0),
            "w": float(size.group(1)), "h": float(size.group(2)),
        })
    lines = []  # courtyard outline, local coordinates
    for kind in ("fp_line", "fp_rect"):
        for child in _sexp_children(block, kind):
            if "CrtYd" not in child:
                continue
            pts = {k: (float(x), float(y)) for k, x, y in _XY_RE.findall(child)}
            if "start" not in pts or "end" not in pts:
                continue
            (x1, y1), (x2, y2) = pts["start"], pts["end"]
            if kind == "fp_line":
                lines.append(((x1, y1), (x2, y2)))
            else:
                lines += [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                          ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]
    return {
        "ref": ref,
        "lib_id": _sexp_unquote(lib.group(1)) if lib else "",
        "x": float(at.group(1)) if at else 0.0,
        "y": float(at.group(2)) if at else 0.0,
        "angle": float(at.group(3) or 0) if at else 0.0,
        "layer": layer,
        "pads": pads,
        "lines": lines,
    }


def parse_pcb_aux_origin(pcb_text: str) -> Tuple[float, float]:
    """(setup (aux_axis_origin X Y)), default (0, 0)."""
    m = re.search(r'\(aux_axis_origin\s+' + _NUM + r'\s+' + _NUM, pcb_text)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def _pcb_footprints(pcb_text: str) -> List[dict]:
    aux = parse_pcb_aux_origin(pcb_text)
    out = []
    for block in _sexp_children(pcb_text, "footprint"):
        fp = _parse_pcb_footprint_block(block)
        fp["aux"] = aux
        out.append(fp)
    return out


def list_pcb_footprints(pcb_text: str) -> List[dict]:
    """[{ref, lib_id, layer, pad_count}], most pads first."""
    fps = [{"ref": f["ref"], "lib_id": f["lib_id"], "layer": f["layer"],
            "pad_count": len(f["pads"])} for f in _pcb_footprints(pcb_text) if f["ref"]]
    return sorted(fps, key=lambda f: (-f["pad_count"], _ref_sort_key(f["ref"])))


def parse_pcb_footprint(pcb_text: str, ref: str) -> dict:
    """Position, angle, layer, pads, courtyard lines and aux origin of one
    footprint. Raises KeyError if ref is not on the board."""
    for fp in _pcb_footprints(pcb_text):
        if fp["ref"] == ref:
            return fp
    raise KeyError(f"{ref} is not on the board")


def _fp_to_board(fp: dict, lx: float, ly: float) -> Tuple[float, float]:
    """Footprint-local -> board (KiCad y-down, angle counter-clockwise on screen)."""
    a = math.radians(fp["angle"])
    c, s = math.cos(a), math.sin(a)
    return fp["x"] + lx * c + ly * s, fp["y"] - lx * s + ly * c


def _board_to_fp(fp: dict, bx: float, by: float) -> Tuple[float, float]:
    a = math.radians(fp["angle"])
    c, s = math.cos(a), math.sin(a)
    dx, dy = bx - fp["x"], by - fp["y"]
    return dx * c - dy * s, dx * s + dy * c


def _pad_board_box(fp: dict, pad: dict) -> Tuple[float, float, float, float]:
    cx, cy = _fp_to_board(fp, pad["x"], pad["y"])
    if pad["shape"] == "circle":
        hx = hy = pad["w"] / 2
    else:  # rect/roundrect/oval/custom: box of the rotated size rectangle
        a = math.radians(pad["angle"])
        c, s = abs(math.cos(a)), abs(math.sin(a))
        hx = pad["w"] / 2 * c + pad["h"] / 2 * s
        hy = pad["w"] / 2 * s + pad["h"] / 2 * c
    return cx - hx, cy - hy, cx + hx, cy + hy


def footprint_pad_center(fp: dict, pads: Optional[List[dict]] = None) -> Tuple[float, float]:
    """Board centre of the merged pad boxes (the plugin's get_position).
    Falls back to the footprint origin when there are no pads."""
    pads = fp["pads"] if pads is None else pads
    if not pads:
        return fp["x"], fp["y"]
    boxes = [_pad_board_box(fp, p) for p in pads]
    return ((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2,
            (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2)


def jlc_rotation(fp: dict, offset: float = 0) -> float:
    rot = fp["angle"] % 360
    if not fp["layer"].startswith("F."):
        rot = (180 - rot) % 360
    return float((rot + offset) % 360) + 0.0


def _subpart_with_defaults(sub: dict) -> dict:
    out = dict(_SUBPART_DEFAULTS)
    out.update(sub)
    out["xy_offset_mm"] = list(out.get("xy_offset_mm") or [0, 0])
    return out


def subpart_placement(fp: dict, subpart: dict) -> dict:
    """Board and CPL position + rotation for one sub-part of parent fp."""
    sub = _subpart_with_defaults(subpart)
    warnings = []
    dx, dy = (float(v) for v in sub["xy_offset_mm"])
    if sub["mode"] == "offset":
        bx, by = _fp_to_board(fp, dx, dy)
        pad_center = None
    elif sub["mode"] == "pads":
        wanted = parse_pad_spec(sub["pads"])
        pads = [p for p in fp["pads"] if p["number"] in wanted]
        if not pads:
            raise ValueError(f"{sub['ref']}: none of pads {sub['pads']!r} are on {fp['ref']}")
        missing = wanted - {p["number"] for p in pads}
        if missing:
            shown = ", ".join(sorted(missing, key=_ref_sort_key)[:8])
            more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
            warnings.append(f"{sub['ref']}: {fp['ref']} has no pad {shown}{more}")
        pad_center = footprint_pad_center(fp, pads)
        lx, ly = _board_to_fp(fp, *pad_center)
        bx, by = _fp_to_board(fp, lx + dx, ly + dy)
    else:
        raise ValueError(f"{sub['ref']}: unknown mode {sub['mode']!r}")
    ax, ay = fp["aux"]
    return {
        "ref": sub["ref"], "parent": fp["ref"],
        "board_x": bx, "board_y": by,
        "cpl_x": bx - ax, "cpl_y": -(by - ay),
        "rotation": jlc_rotation(fp, float(sub["rotation_offset"])),
        "layer": "top" if fp["layer"].startswith("F.") else "bottom",
        "local": _board_to_fp(fp, bx, by),
        "pad_center_local": _board_to_fp(fp, *pad_center) if pad_center else None,
        "warnings": warnings,
    }


def apply_preset(preset: dict) -> List[dict]:
    """Sub-part rows for the config, preset-level fields filled into each."""
    shared = {k: preset[k] for k in ("comment", "footprint", "lcsc", "rotation_offset")
              if k in preset}
    return [_subpart_with_defaults({**shared, **sub}) for sub in preset.get("subparts", [])]


def check_preset(placement: dict, subpart: dict) -> Optional[str]:
    """Warning when the pad centre is not where the preset measured it."""
    expect = subpart.get("expect_local_mm")
    got = placement.get("pad_center_local")
    if not expect or got is None:
        return None
    dist = math.hypot(got[0] - expect[0], got[1] - expect[1])
    if dist <= _MULTIPART_PRESET_TOLERANCE_MM:
        return None
    return (f"{subpart['ref']}: pad centre is {dist:.2f} mm from where the preset expects it "
            f"(got {got[0]:.2f}, {got[1]:.2f}; expected {expect[0]:.2f}, {expect[1]:.2f}). "
            f"Different footprint origin, or the pad ranges are swapped?")


def multipart_placements(pcb_text: str, config: dict) -> Tuple[Dict[str, dict], List[str]]:
    """{sub-part ref: placement} for every parent in config, plus warnings."""
    return _multipart_placements_fps({f["ref"]: f for f in _pcb_footprints(pcb_text)}, config)


def _multipart_placements_fps(fps: Dict[str, dict],
                              config: dict) -> Tuple[Dict[str, dict], List[str]]:
    placements, warnings = {}, []
    for parent in config.get("parents", []):
        fp = fps.get(parent["ref"])
        if fp is None:
            raise KeyError(f"{parent['ref']} is not on the board")
        for sub in parent.get("subparts", []):
            p = subpart_placement(fp, sub)
            placements[p["ref"]] = p
            warnings += p["warnings"]
            w = check_preset(p, sub)
            if w:
                warnings.append(w)
    return placements, warnings


def find_ghosts(pcb_text: str, config: dict, placements: Dict[str, dict]) -> List[dict]:
    """Footprints on the board that use a sub-part's ref, and how far they are
    from the computed position."""
    return _find_ghosts_fps({f["ref"]: f for f in _pcb_footprints(pcb_text)}, placements)


def _find_ghosts_fps(fps: Dict[str, dict], placements: Dict[str, dict]) -> List[dict]:
    out = []
    for fp in fps.values():
        p = placements.get(fp["ref"])
        if p is None:
            continue
        gx, gy = footprint_pad_center(fp)
        out.append({"ref": fp["ref"], "board_x": gx, "board_y": gy,
                    "rotation": jlc_rotation(fp),
                    "distance_mm": math.hypot(gx - p["board_x"], gy - p["board_y"])})
    return out


# ───── CSV patching ───────────────────────────────────────────────────────

def _cpl_num(v: float) -> str:
    return str(round(v, 6) + 0.0)


def _remove_refs(config: dict) -> Tuple[Set[str], List[dict]]:
    """(refs whose existing rows go away, all sub-parts in order)."""
    refs, subs = set(), []
    for parent in config.get("parents", []):
        if parent.get("drop_parent_from_cpl", True):
            refs.add(parent["ref"])
        for sub in parent.get("subparts", []):
            sub = _subpart_with_defaults(sub)
            refs.add(sub["ref"])
            subs.append(sub)
    return refs, subs


def _col(header: List[str], name: str) -> int:
    try:
        return header.index(name)
    except ValueError:
        raise ValueError(f"CSV has no {name!r} column (header: {', '.join(header)})")


def patch_cpl(header: List[str], rows: List[List[str]], config: dict,
              placements: Dict[str, dict]) -> Tuple[List[List[str]], List[dict]]:
    """Drop parent rows, add or replace one row per sub-part. Other rows keep
    their order; new rows go where the plugin's sort-by-reference puts them.
    Returns (rows, diff) with diff entries {kind, ref, old, new}."""
    ci = {n: _col(header, n) for n in ("Designator", "Val", "Package", "Mid X", "Mid Y",
                                       "Rotation", "Layer")}
    drop, subs = _remove_refs(config)
    old = {r[ci["Designator"]]: r for r in rows if r and r[ci["Designator"]] in drop}
    kept = [r for r in rows if not (r and r[ci["Designator"]] in drop)]
    diff = []
    for sub in subs:
        p = placements[sub["ref"]]
        new = [""] * len(header)
        for name, value in (("Designator", sub["ref"]), ("Val", sub["comment"]),
                            ("Package", sub["footprint"]), ("Mid X", _cpl_num(p["cpl_x"])),
                            ("Mid Y", _cpl_num(p["cpl_y"])), ("Rotation", str(p["rotation"])),
                            ("Layer", p["layer"])):
            new[ci[name]] = value
        at = next((i for i, r in enumerate(kept) if r and r[ci["Designator"]] > sub["ref"]),
                  len(kept))
        kept.insert(at, new)
        prev = old.pop(sub["ref"], None)
        kind = "added" if prev is None else ("unchanged" if prev == new else "replaced")
        diff.append({"kind": kind, "ref": sub["ref"], "old": prev, "new": new})
    for ref, prev in old.items():
        diff.append({"kind": "removed", "ref": ref, "old": prev, "new": None})
    return kept, diff


def patch_bom(header: List[str], rows: List[List[str]],
              config: dict) -> Tuple[List[List[str]], List[dict]]:
    """Take sub-part and parent refs out of every BOM row (dropping rows left
    empty), then add one row per (comment, footprint, LCSC) group of
    sub-parts, in the slot of the first row that held any of its refs."""
    ci = {n: _col(header, n) for n in ("Comment", "Designator", "Footprint", "LCSC")}
    qi = header.index("Quantity") if "Quantity" in header else None
    drop, subs = _remove_refs(config)
    groups: Dict[Tuple[str, str, str], List[str]] = {}
    for sub in subs:
        groups.setdefault((sub["comment"], sub["footprint"], sub["lcsc"]), []).append(sub["ref"])
    group_of = {ref: key for key, refs in groups.items() for ref in refs}

    out: List[Optional[List[str]]] = []
    slots: Dict[Tuple[str, str, str], int] = {}
    slot_old: Dict[Tuple[str, str, str], List[str]] = {}
    diff = []
    for r in rows:
        refs = [x.strip() for x in r[ci["Designator"]].split(",") if x.strip()] if r else []
        hit = [x for x in refs if x in drop]
        if not hit:
            out.append(r)
            continue
        for ref in hit:
            key = group_of.get(ref)
            if key and key not in slots:
                slots[key] = len(out)
                slot_old[key] = r
                out.append(None)  # filled below
        left = [x for x in refs if x not in drop]
        if left:
            new = list(r)
            new[ci["Designator"]] = ",".join(left)
            if qi is not None:
                new[qi] = str(len(left))
            out.append(new)
            diff.append({"kind": "changed", "ref": ",".join(hit), "old": r, "new": new})
        elif not any(group_of.get(x) and slot_old.get(group_of[x]) is r for x in hit):
            diff.append({"kind": "removed", "ref": ",".join(hit), "old": r, "new": None})
    for key, refs in groups.items():
        new = [""] * len(header)
        new[ci["Comment"]], new[ci["Footprint"]], new[ci["LCSC"]] = key
        new[ci["Designator"]] = ",".join(refs)
        if qi is not None:
            new[qi] = str(len(refs))
        if key in slots:
            out[slots[key]] = new
            prev = slot_old[key]
            kind = "unchanged" if prev == new else "replaced"
        else:
            out.append(new)
            prev, kind = None, "added"
        diff.append({"kind": kind, "ref": ",".join(refs), "old": prev, "new": new})
    return [r for r in out if r is not None], diff


def read_csv_rows(path: Path) -> Tuple[List[str], List[List[str]]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"{path.name} is empty")
    return rows[0], rows[1:]


def write_csv_with_backup(path: Path, header: List[str], rows: List[List[str]]) -> bool:
    """Write like the plugin does (csv, newline='', UTF-8). Copies the old file
    to <file>.bak first. Returns False (and writes nothing) if unchanged."""
    path = Path(path)
    if path.exists():
        if read_csv_rows(path) == (header, rows):
            return False
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(header)
        writer.writerows(rows)
    return True


# ───── Config, presets, paths ─────────────────────────────────────────────

def jlc_output_paths(pro_path: Path) -> Dict[str, Path]:
    """The board and the plugin's BOM/CPL for a .kicad_pro."""
    pro_path = Path(pro_path)
    pcb = pro_path.with_suffix(".kicad_pcb")
    out_dir = pro_path.parent / "jlcpcb" / "production_files"
    return {"pcb": pcb, "bom": out_dir / f"BOM-{pcb.stem}.csv",
            "cpl": out_dir / f"CPL-{pcb.stem}.csv"}


def multipart_config_path(pro_path: Path) -> Path:
    pro_path = Path(pro_path)
    return pro_path.with_name(pro_path.stem + ".multipart.json")


def multipart_config_load(pro_path: Path) -> dict:
    """The project's config, or an empty one. Raises ValueError if the file
    is broken or from a newer PartBench."""
    path = multipart_config_path(pro_path)
    if not path.exists():
        return {"version": MULTIPART_CONFIG_VERSION, "parents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} is not valid JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("parents", []), list):
        raise ValueError(f"{path.name} is not a multi-part config")
    if data.get("version", 1) > MULTIPART_CONFIG_VERSION:
        raise ValueError(f"{path.name} is version {data['version']}; "
                         f"this PartBench reads version {MULTIPART_CONFIG_VERSION}")
    data["version"] = MULTIPART_CONFIG_VERSION
    for parent in data.get("parents", []):
        parent.setdefault("drop_parent_from_cpl", True)
        parent["subparts"] = [_subpart_with_defaults(s) for s in parent.get("subparts", [])]
    data.setdefault("parents", [])
    return data


def multipart_config_save(pro_path: Path, config: dict) -> Path:
    path = multipart_config_path(pro_path)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _multipart_presets_file() -> Path:
    # Read PASSIVES_LIB_DIR at call time so tests can redirect it
    return PASSIVES_LIB_DIR / "multipart_presets.json"


def multipart_presets_all() -> Dict[str, dict]:
    """Built-in presets, then the user's (a user preset can't hide a built-in)."""
    presets = dict(MULTIPART_PRESETS)
    path = _multipart_presets_file()
    try:
        user = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        user = {}
    for name, preset in user.items():
        if name not in presets and isinstance(preset, dict):
            presets[name] = preset
    return presets


def multipart_preset_save_user(name: str, preset: dict) -> None:
    if name in MULTIPART_PRESETS:
        raise ValueError(f"{name!r} is a built-in preset; pick another name")
    path = _multipart_presets_file()
    try:
        user = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        user = {}
    user[name] = preset
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(user, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ───── Tab UI ─────────────────────────────────────────────────────────────

_MP_GROUP_COLORS = ["#1f6feb", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#39c5cf"]
_MP_SUB_COLS = ("ref", "mode", "pads", "comment", "footprint", "lcsc", "rot", "offset", "cpl")


def _create_multipart_tab(self, parent):
    """Create the Multi-part CPL tab: place one symbol's sub-parts in the JLC CPL/BOM."""
    parent.grid_columnconfigure(0, weight=1)
    parent.grid_rowconfigure(1, weight=1)

    # State
    self.mp_project = tk.StringVar()
    self.mp_status = tk.StringVar(
        value="Pick a .kicad_pro. Run the JLCPCB plugin first so the BOM/CPL exist.")
    self.mp_parent_var = tk.StringVar()
    self.mp_preset_var = tk.StringVar()
    self.mp_drop_parent = tk.BooleanVar(value=True)
    self.mp_warn_var = tk.StringVar()
    self.mp_config = {"version": MULTIPART_CONFIG_VERSION, "parents": []}
    self.mp_fps = {}            # parsed board: ref -> footprint
    self.mp_paths = {}          # pcb / bom / cpl
    self.mp_parent_labels = {}  # combobox label -> ref
    self.mp_preview = None      # result of the last Preview, needed to Patch
    self._mp_edit = None        # open sub-part dialog
    self._mp_view = None        # canvas transform

    # ── Section 1: project ──
    file_frame = ttk.Labelframe(parent, text="1. Project", padding=12)
    file_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
    file_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(file_frame, text="KiCad Project:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(file_frame, textvariable=self.mp_project, state="readonly").grid(
        row=0, column=1, sticky="ew", padx=4, pady=4)
    ttk.Button(file_frame, text="📁 Browse", command=self._mp_browse_project).grid(
        row=0, column=2, padx=4, pady=4)
    ttk.Button(file_frame, text="🔄 Reload", command=self._mp_reload).grid(
        row=0, column=3, padx=4, pady=4)
    ttk.Label(file_frame, textvariable=self.mp_status, foreground=COLORS['text_muted']).grid(
        row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

    body = ttk.Frame(parent)
    body.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    # uniform: split by weight, not by how wide the table asks to be
    body.grid_columnconfigure(0, weight=2, uniform="mp")
    body.grid_columnconfigure(1, weight=3, uniform="mp")
    body.grid_rowconfigure(0, weight=1)

    # ── Left: parent, preset, sub-parts ──
    left = ttk.Labelframe(body, text="2. Parent and Sub-parts", padding=10)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    left.grid_columnconfigure(1, weight=1)
    left.grid_rowconfigure(5, weight=1)

    ttk.Label(left, text="Parent footprint:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
    self.mp_parent_combo = ttk.Combobox(left, textvariable=self.mp_parent_var, state="readonly")
    self.mp_parent_combo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
    self.mp_parent_combo.bind("<<ComboboxSelected>>", lambda e: self._mp_on_parent_change())
    ttk.Checkbutton(left, text="Drop the parent's own CPL/BOM row",
                    variable=self.mp_drop_parent, command=self._mp_on_drop_toggle).grid(
        row=1, column=1, columnspan=2, sticky="w", pady=3)

    ttk.Label(left, text="Preset:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
    self.mp_preset_combo = ttk.Combobox(left, textvariable=self.mp_preset_var, state="readonly")
    self.mp_preset_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
    preset_btns = ttk.Frame(left)
    preset_btns.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 3))
    ttk.Button(preset_btns, text="Apply Preset", command=self._mp_apply_preset).pack(side="left", padx=2)
    ttk.Button(preset_btns, text="Save as Preset…", command=self._mp_save_preset).pack(side="left", padx=2)
    self._mp_refresh_presets()

    ttk.Label(left, text="pads: centre of the listed pads (offset nudges it).   "
                         "offset: X/Y from the parent origin; click the preview to place.   "
                         "Offsets are in the parent's own frame, like the footprint editor.",
              foreground=COLORS['text_muted'], font=("Segoe UI", 8),
              wraplength=520, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 4))

    tree_frame = ttk.Frame(left)
    tree_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
    tree_frame.grid_columnconfigure(0, weight=1)
    tree_frame.grid_rowconfigure(0, weight=1)
    self.mp_sub_tree = ttk.Treeview(tree_frame, columns=_MP_SUB_COLS, show="headings",
                                    height=8, selectmode="browse")
    self.mp_sub_tree.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.mp_sub_tree.yview)
    vsb.grid(row=0, column=1, sticky="ns")
    hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.mp_sub_tree.xview)
    hsb.grid(row=1, column=0, sticky="ew")
    self.mp_sub_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    for col, text, width in (("ref", "Ref", 50), ("mode", "Mode", 55), ("pads", "Pads", 70),
                             ("comment", "Comment", 140), ("footprint", "Footprint", 140),
                             ("lcsc", "LCSC", 80), ("rot", "Rot +", 45), ("offset", "Offset", 75),
                             ("cpl", "CPL X / Y / Rot", 140)):
        self.mp_sub_tree.heading(col, text=text)
        self.mp_sub_tree.column(col, width=width, anchor="w",
                                stretch=col in ("comment", "footprint", "cpl"))
    self.mp_sub_tree.bind("<Double-Button-1>", lambda e: self._mp_edit_subpart())
    self.mp_sub_tree.bind("<<TreeviewSelect>>", lambda e: self._mp_redraw())

    sub_btns = ttk.Frame(left)
    sub_btns.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(6, 0))
    ttk.Button(sub_btns, text="➕ Add Sub-part", command=self._mp_add_subpart).pack(side="left", padx=2)
    ttk.Button(sub_btns, text="Edit…", command=self._mp_edit_subpart).pack(side="left", padx=2)
    ttk.Button(sub_btns, text="Remove", command=self._mp_remove_subpart).pack(side="left", padx=2)

    ttk.Label(left, textvariable=self.mp_warn_var, foreground=COLORS['warning'],
              wraplength=520, justify="left").grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

    # ── Right: preview canvas + diff ──
    right = ttk.Frame(body)
    right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
    right.grid_columnconfigure(0, weight=1)
    right.grid_rowconfigure(0, weight=3)
    right.grid_rowconfigure(1, weight=2)

    canvas_frame = ttk.Labelframe(right, text="3. Preview (board view, as in the PCB editor)", padding=6)
    canvas_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
    canvas_frame.grid_columnconfigure(0, weight=1)
    canvas_frame.grid_rowconfigure(0, weight=1)
    self.mp_canvas = tk.Canvas(canvas_frame, background=COLORS['panel'], highlightthickness=0,
                               width=600, height=320)
    self.mp_canvas.grid(row=0, column=0, sticky="nsew")
    self.mp_canvas.bind("<Configure>", lambda e: self._mp_redraw())
    self.mp_canvas.bind("<Button-1>", self._mp_canvas_click)

    diff_frame = ttk.Labelframe(right, text="4. Changes to the CSVs", padding=6)
    diff_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
    diff_frame.grid_columnconfigure(0, weight=1)
    diff_frame.grid_rowconfigure(0, weight=1)
    self.mp_diff_tree = ttk.Treeview(diff_frame, columns=("file", "change", "ref", "before", "after"),
                                     show="headings", height=6)
    self.mp_diff_tree.grid(row=0, column=0, sticky="nsew")
    dsb = ttk.Scrollbar(diff_frame, orient="vertical", command=self.mp_diff_tree.yview)
    dsb.grid(row=0, column=1, sticky="ns")
    self.mp_diff_tree.configure(yscrollcommand=dsb.set)
    for col, text, width in (("file", "File", 45), ("change", "Change", 90), ("ref", "Ref", 70),
                             ("before", "Before", 260), ("after", "After", 260)):
        self.mp_diff_tree.heading(col, text=text)
        self.mp_diff_tree.column(col, width=width, anchor="w", stretch=col in ("before", "after"))
    self.mp_diff_tree.tag_configure("removed", foreground=COLORS['error'])
    self.mp_diff_tree.tag_configure("added", foreground=COLORS['success'])
    self.mp_diff_tree.tag_configure("replaced", foreground=COLORS['warning'])
    self.mp_diff_tree.tag_configure("changed", foreground=COLORS['warning'])
    self.mp_diff_tree.tag_configure("unchanged", foreground=COLORS['text_muted'])

    # ── Actions ──
    actions = ttk.Frame(parent)
    actions.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
    ttk.Button(actions, text="👁 Preview", command=self._mp_run_preview,
               style="Accent.TButton").pack(side="left", padx=4)
    self.mp_patch_btn = ttk.Button(actions, text="✏️ Patch CSVs", command=self._mp_patch,
                                   state="disabled")
    self.mp_patch_btn.pack(side="left", padx=4)
    ttk.Button(actions, text="💾 Save Config", command=self._mp_save_config).pack(side="left", padx=4)
    ttk.Label(actions, text="Patch writes <file>.csv.bak first, and is safe to run again.",
              foreground=COLORS['text_muted']).pack(side="left", padx=12)


# ───── Tab actions ────────────────────────────────────────────────────────

def _mp_browse_project(self):
    fname = filedialog.askopenfilename(
        title="Select KiCad Project",
        filetypes=[("KiCad Project", "*.kicad_pro"), ("All Files", "*.*")])
    if fname:
        self._mp_load_project(Path(fname))


def _mp_reload(self):
    if self.mp_project.get():
        self._mp_load_project(Path(self.mp_project.get()), keep_config=True)


def _mp_load_project(self, pro: Path, keep_config: bool = False):
    """Parse the board, find the plugin's CSVs and load the project's config."""
    pro = Path(pro)
    paths = jlc_output_paths(pro)
    if not paths["pcb"].exists():
        messagebox.showerror("No Board", f"Can't find the board next to the project:\n{paths['pcb']}")
        return
    try:
        fps = {f["ref"]: f for f in _pcb_footprints(paths["pcb"].read_text(encoding="utf-8")) if f["ref"]}
    except (OSError, ValueError) as e:
        messagebox.showerror("Parse Error", f"Failed to read the board:\n{e}")
        return
    if not keep_config:
        try:
            self.mp_config = multipart_config_load(pro)
        except ValueError as e:
            messagebox.showerror("Config Error", f"{e}\n\nStarting with an empty config.")
            self.mp_config = {"version": MULTIPART_CONFIG_VERSION, "parents": []}
    self.mp_project.set(str(pro))
    self.mp_paths, self.mp_fps = paths, fps

    def mark(key):
        return ("✓ " if paths[key].exists() else "✗ ") + paths[key].name
    cfg = multipart_config_path(pro)
    self.mp_status.set(f"{mark('pcb')}   ·   {mark('cpl')}   ·   {mark('bom')}   ·   "
                       f"config: {cfg.name if cfg.exists() else 'new'}")

    by_pads = sorted(fps.values(), key=lambda f: (-len(f["pads"]), _ref_sort_key(f["ref"])))
    self.mp_parent_labels = {f"{f['ref']} — {len(f['pads'])} pads · {f['lib_id']}": f["ref"]
                             for f in by_pads}
    self.mp_parent_combo["values"] = list(self.mp_parent_labels)
    configured = [p["ref"] for p in self.mp_config["parents"] if p["ref"] in fps]
    first = configured[0] if configured else (by_pads[0]["ref"] if by_pads else "")
    self._mp_select_parent(first)


def _mp_select_parent(self, ref: str):
    label = next((lbl for lbl, r in self.mp_parent_labels.items() if r == ref), "")
    self.mp_parent_var.set(label)
    self._mp_on_parent_change()


def _mp_current_ref(self) -> str:
    return self.mp_parent_labels.get(self.mp_parent_var.get(), "")


def _mp_parent_entry(self, create: bool = False) -> Optional[dict]:
    ref = self._mp_current_ref()
    if not ref:
        return None
    for p in self.mp_config["parents"]:
        if p["ref"] == ref:
            return p
    if not create:
        return None
    entry = {"ref": ref, "drop_parent_from_cpl": self.mp_drop_parent.get(), "subparts": []}
    self.mp_config["parents"].append(entry)
    return entry


def _mp_on_parent_change(self):
    entry = self._mp_parent_entry()
    self.mp_drop_parent.set(entry.get("drop_parent_from_cpl", True) if entry else True)
    if entry and entry.get("preset") in multipart_presets_all():
        self.mp_preset_var.set(entry["preset"])
    self._mp_changed()


def _mp_on_drop_toggle(self):
    entry = self._mp_parent_entry(create=True)
    if entry:
        entry["drop_parent_from_cpl"] = self.mp_drop_parent.get()
    self._mp_changed()


def _mp_refresh_presets(self):
    names = list(multipart_presets_all())
    self.mp_preset_combo["values"] = names
    if self.mp_preset_var.get() not in names:
        self.mp_preset_var.set(names[0] if names else "")


def _mp_effective_config(self) -> dict:
    """The config without parents that have no sub-parts (or aren't on the board)."""
    parents = [p for p in self.mp_config["parents"] if p.get("subparts") and p["ref"] in self.mp_fps]
    return {"version": MULTIPART_CONFIG_VERSION, "parents": parents}


def _mp_compute(self):
    """(placements, warnings, ghosts) for the current config; errors become warnings."""
    try:
        placements, warnings = _multipart_placements_fps(self.mp_fps, self._mp_effective_config())
    except (ValueError, KeyError) as e:
        return {}, [str(e).strip("'\"")], []
    return placements, warnings, _find_ghosts_fps(self.mp_fps, placements)


def _mp_changed(self):
    """Anything edited: the old Preview no longer applies."""
    self.mp_preview = None
    if hasattr(self, "mp_patch_btn"):
        self.mp_patch_btn.configure(state="disabled")
        for iid in self.mp_diff_tree.get_children():
            self.mp_diff_tree.delete(iid)
    self._mp_refresh_subparts()


def _mp_refresh_subparts(self):
    placements, warnings, ghosts = self._mp_compute()
    selected = self.mp_sub_tree.selection()
    for iid in self.mp_sub_tree.get_children():
        self.mp_sub_tree.delete(iid)
    entry = self._mp_parent_entry()
    for i, sub in enumerate(entry["subparts"] if entry else []):
        sub = _subpart_with_defaults(sub)
        p = placements.get(sub["ref"])
        cpl = f"{p['cpl_x']:.3f} / {p['cpl_y']:.3f} / {p['rotation']:g}" if p else "—"
        dx, dy = sub["xy_offset_mm"]
        self.mp_sub_tree.insert("", "end", iid=str(i), values=(
            sub["ref"], sub["mode"], sub["pads"] if sub["mode"] == "pads" else "",
            sub["comment"], sub["footprint"], sub["lcsc"], f"{sub['rotation_offset']:g}",
            f"{dx:g}, {dy:g}", cpl))
    for iid in selected:
        if self.mp_sub_tree.exists(iid):
            self.mp_sub_tree.selection_set(iid)
    for g in ghosts:
        if g["distance_mm"] > _MULTIPART_PRESET_TOLERANCE_MM:
            warnings.append(f"{g['ref']} is also on the board as its own footprint, "
                            f"{g['distance_mm']:.2f} mm from the computed spot (drawn in red).")
    self.mp_warn_var.set("\n".join("⚠ " + w for w in warnings))
    self._mp_redraw(placements, ghosts)


def _mp_board_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
    scale, ox, oy, minx, miny = self._mp_view
    return ox + (x - minx) * scale, oy + (y - miny) * scale


def _mp_redraw(self, placements=None, ghosts=None):
    c = self.mp_canvas
    c.delete("all")
    fp = self.mp_fps.get(self._mp_current_ref())
    if fp is None:
        self._mp_view = None
        c.create_text(12, 12, anchor="nw", fill=COLORS['text_muted'],
                      text="Load a project and pick the parent footprint.")
        return
    if placements is None:
        placements, _warnings, ghosts = self._mp_compute()
    entry = self._mp_parent_entry()
    subs = [_subpart_with_defaults(s) for s in (entry["subparts"] if entry else [])]
    my_ghosts = [g for g in ghosts or [] if g["ref"] in {s["ref"] for s in subs}]

    # Group colour per pad number
    pad_color = {}
    for i, sub in enumerate(subs):
        if sub["mode"] == "pads":
            try:
                for num in parse_pad_spec(sub["pads"]):
                    pad_color.setdefault(num, _MP_GROUP_COLORS[i % len(_MP_GROUP_COLORS)])
            except ValueError:
                pass

    pad_boxes = [(_pad_board_box(fp, p), p) for p in fp["pads"]]
    lines = [(_fp_to_board(fp, *a), _fp_to_board(fp, *b)) for a, b in fp["lines"]]
    ghost_boxes = []
    for g in my_ghosts:
        gfp = self.mp_fps[g["ref"]]
        boxes = [_pad_board_box(gfp, p) for p in gfp["pads"]] or [(g["board_x"], g["board_y"]) * 2]
        ghost_boxes.append((g, (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                max(b[2] for b in boxes), max(b[3] for b in boxes))))

    xs = [b[0] for b, _ in pad_boxes] + [b[2] for b, _ in pad_boxes]
    ys = [b[1] for b, _ in pad_boxes] + [b[3] for b, _ in pad_boxes]
    for a, b in lines:
        xs += [a[0], b[0]]
        ys += [a[1], b[1]]
    for _g, b in ghost_boxes:
        xs += [b[0], b[2]]
        ys += [b[1], b[3]]
    for p in placements.values():
        if p["parent"] == fp["ref"]:
            xs.append(p["board_x"])
            ys.append(p["board_y"])
    if not xs:
        xs, ys = [fp["x"] - 5, fp["x"] + 5], [fp["y"] - 5, fp["y"] + 5]
    w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    margin = 40
    scale = min((w - 2 * margin) / max(maxx - minx, 1e-3), (h - 2 * margin) / max(maxy - miny, 1e-3))
    ox = (w - (maxx - minx) * scale) / 2
    oy = (h - (maxy - miny) * scale) / 2
    self._mp_view = (scale, ox, oy, minx, miny)
    to_c = self._mp_board_to_canvas

    for a, b in lines:
        c.create_line(*to_c(*a), *to_c(*b), fill=COLORS['text_muted'], dash=(3, 3))
    for (x1, y1, x2, y2), pad in pad_boxes:
        color = pad_color.get(pad["number"], COLORS['border'])
        if pad["type"] == "np_thru_hole":
            c.create_oval(*to_c(x1, y1), *to_c(x2, y2), outline=COLORS['text_muted'])
        else:
            c.create_rectangle(*to_c(x1, y1), *to_c(x2, y2), fill=color, outline="")

    # Pin-1 marker: lowest-numbered pad of each group
    for i, sub in enumerate(subs):
        if sub["mode"] != "pads":
            continue
        try:
            wanted = parse_pad_spec(sub["pads"])
        except ValueError:
            continue
        group = [p for p in fp["pads"] if p["number"] in wanted]
        if group:
            first = min(group, key=lambda p: _ref_sort_key(p["number"]))
            px, py = to_c(*_fp_to_board(fp, first["x"], first["y"]))
            c.create_oval(px - 6, py - 6, px + 6, py + 6, outline=COLORS['text'], width=2)
            c.create_text(px - 8, py - 8, anchor="se", fill=COLORS['text_muted'], font=("Segoe UI", 8),
                          text=f"pin {first['number']}")

    # Ghost footprints left on the board
    for g, (x1, y1, x2, y2) in ghost_boxes:
        target = placements[g["ref"]]
        bad = g["distance_mm"] > _MULTIPART_PRESET_TOLERANCE_MM
        color = COLORS['error'] if bad else COLORS['text_muted']
        c.create_rectangle(*to_c(x1, y1), *to_c(x2, y2), outline=color, dash=(4, 2))
        if not bad:  # ghost already sits on the computed spot
            continue
        c.create_line(*to_c(g["board_x"], g["board_y"]),
                      *to_c(target["board_x"], target["board_y"]), fill=color, dash=(2, 2), arrow="last")
        gx, gy = to_c(x1, y2)
        c.create_text(gx, gy + 4, anchor="nw", fill=color, font=("Segoe UI", 8),
                      text=f"ghost {g['ref']}: {g['distance_mm']:.2f} mm off")

    # Computed sub-part centres
    sel = self.mp_sub_tree.selection()
    sel_ref = subs[int(sel[0])]["ref"] if sel and int(sel[0]) < len(subs) else None
    for i, sub in enumerate(subs):
        p = placements.get(sub["ref"])
        if not p:
            continue
        x, y = to_c(p["board_x"], p["board_y"])
        color = _MP_GROUP_COLORS[i % len(_MP_GROUP_COLORS)]
        r = 14 if sub["ref"] == sel_ref else 10
        c.create_line(x - r, y, x + r, y, fill=COLORS['text'], width=2)
        c.create_line(x, y - r, x, y + r, fill=COLORS['text'], width=2)
        c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=color, width=2)
        c.create_text(x, y + r + 6, anchor="n", fill=COLORS['text'], font=("Segoe UI", 9, "bold"),
                      text=f"{sub['ref']}  X {p['cpl_x']:.3f}  Y {p['cpl_y']:.3f}  R {p['rotation']:g}")


def _mp_canvas_click(self, event):
    """Place an offset-mode sub-part where the preview was clicked."""
    fp = self.mp_fps.get(self._mp_current_ref())
    if fp is None or self._mp_view is None:
        return
    scale, ox, oy, minx, miny = self._mp_view
    lx, ly = _board_to_fp(fp, minx + (event.x - ox) / scale, miny + (event.y - oy) / scale)
    lx, ly = round(lx, 3) + 0.0, round(ly, 3) + 0.0
    if self._mp_edit and self._mp_edit["dlg"].winfo_exists():
        v = self._mp_edit["vars"]
        if v["mode"].get() == "offset":
            v["dx"].set(f"{lx:g}")
            v["dy"].set(f"{ly:g}")
        return
    entry = self._mp_parent_entry()
    sel = self.mp_sub_tree.selection()
    if not (entry and sel):
        return
    sub = entry["subparts"][int(sel[0])]
    if sub.get("mode", "pads") != "offset":
        self.mp_warn_var.set("Clicking places offset-mode sub-parts only. "
                             f"{sub['ref']} is placed from its pads.")
        return
    sub["xy_offset_mm"] = [lx, ly]
    self._mp_changed()


def _mp_apply_preset(self):
    name = self.mp_preset_var.get()
    preset = multipart_presets_all().get(name)
    if not preset or not self._mp_current_ref():
        messagebox.showwarning("No Parent", "Load a project and pick the parent footprint first.")
        return
    entry = self._mp_parent_entry(create=True)
    if entry["subparts"] and not messagebox.askyesno(
            "Replace Sub-parts?", f"Replace the {len(entry['subparts'])} sub-part(s) of "
                                  f"{entry['ref']} with the preset '{name}'?"):
        return
    entry["subparts"] = apply_preset(preset)
    entry["preset"] = name
    self._mp_changed()


def _mp_save_preset(self):
    entry = self._mp_parent_entry()
    if not entry or not entry["subparts"]:
        messagebox.showwarning("Nothing to Save", "Add sub-parts to the parent first.")
        return
    dlg = tk.Toplevel(self)
    dlg.title("Save as Preset")
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    dlg.grab_set()
    name = tk.StringVar(value=entry.get("preset", "") if entry.get("preset") not in MULTIPART_PRESETS else "")
    ttk.Label(dlg, text="Preset name:").pack(anchor="w", padx=12, pady=(12, 4))
    ttk.Entry(dlg, textvariable=name, width=48).pack(fill="x", padx=12)
    ttk.Label(dlg, text="Saves this parent's sub-parts, including measured pad centres, "
                        "so the next board like this is one click.",
              foreground=COLORS['text_muted'], wraplength=380, justify="left").pack(anchor="w", padx=12, pady=6)

    def do_save():
        n = name.get().strip()
        if not n:
            messagebox.showwarning("No Name", "Type a name for the preset.", parent=dlg)
            return
        placements, _warnings, _ghosts = self._mp_compute()
        subs = []
        for sub in entry["subparts"]:
            sub = {k: v for k, v in _subpart_with_defaults(sub).items() if k != "expect_local_mm"}
            p = placements.get(sub["ref"])
            if sub["mode"] == "pads" and p and p["pad_center_local"]:
                sub["expect_local_mm"] = [round(v, 3) + 0.0 for v in p["pad_center_local"]]
            subs.append(sub)
        try:
            multipart_preset_save_user(n, {"subparts": subs})
        except (ValueError, OSError) as e:
            messagebox.showerror("Save Failed", str(e), parent=dlg)
            return
        entry["preset"] = n
        dlg.destroy()
        self._mp_refresh_presets()
        self.mp_preset_var.set(n)
        messagebox.showinfo("Preset Saved", f"Saved preset '{n}' to\n{_multipart_presets_file()}")

    btns = ttk.Frame(dlg)
    btns.pack(fill="x", padx=12, pady=12)
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
    ttk.Button(btns, text="Save Preset", command=do_save, style="Accent.TButton").pack(side="right", padx=4)


def _mp_add_subpart(self):
    if not self._mp_current_ref():
        messagebox.showwarning("No Parent", "Load a project and pick the parent footprint first.")
        return
    entry = self._mp_parent_entry(create=True)
    taken = {s["ref"] for p in self.mp_config["parents"] for s in p["subparts"]}
    n = 1
    while f"J{n}" in taken:
        n += 1
    entry["subparts"].append(_subpart_with_defaults({"ref": f"J{n}"}))
    self._mp_changed()
    self._mp_edit_subpart(len(entry["subparts"]) - 1, is_new=True)


def _mp_remove_subpart(self):
    entry = self._mp_parent_entry()
    sel = self.mp_sub_tree.selection()
    if not (entry and sel):
        return
    del entry["subparts"][int(sel[0])]
    self._mp_changed()


def _mp_edit_subpart(self, index: Optional[int] = None, is_new: bool = False):
    """Edit one sub-part. The preview follows the fields as you type; Cancel puts it back."""
    entry = self._mp_parent_entry()
    if index is None:
        sel = self.mp_sub_tree.selection()
        if not sel:
            return
        index = int(sel[0])
    if not entry or index >= len(entry["subparts"]):
        return
    if self._mp_edit and self._mp_edit["dlg"].winfo_exists():
        self._mp_edit["dlg"].destroy()
    orig = json.loads(json.dumps(entry["subparts"][index]))
    sub = _subpart_with_defaults(orig)
    self.mp_sub_tree.selection_set(str(index))

    dlg = tk.Toplevel(self)
    dlg.title(f"Sub-part of {entry['ref']}")
    dlg.configure(bg=COLORS['bg'])
    dlg.transient(self)
    form = ttk.Frame(dlg)
    form.pack(fill="both", expand=True, padx=12, pady=12)
    form.grid_columnconfigure(1, weight=1)
    v = {k: tk.StringVar(value=str(sub[k])) for k in ("ref", "mode", "pads", "comment", "footprint", "lcsc")}
    v["rot"] = tk.StringVar(value=f"{sub['rotation_offset']:g}")
    v["dx"] = tk.StringVar(value=f"{sub['xy_offset_mm'][0]:g}")
    v["dy"] = tk.StringVar(value=f"{sub['xy_offset_mm'][1]:g}")
    fields = (("ref", "Reference (CPL Designator):"), ("mode", "Mode:"), ("pads", "Pads (e.g. 1-100, 101, EP):"),
              ("comment", "Comment (BOM) / Val (CPL):"), ("footprint", "Footprint / Package:"),
              ("lcsc", "LCSC:"), ("rot", "Rotation offset (°):"), ("dx", "X offset (mm):"),
              ("dy", "Y offset (mm):"))
    for r, (key, label) in enumerate(fields):
        ttk.Label(form, text=label).grid(row=r, column=0, sticky="e", padx=(0, 6), pady=3)
        if key == "mode":
            ttk.Combobox(form, textvariable=v[key], values=["pads", "offset"], state="readonly",
                         width=10).grid(row=r, column=1, sticky="w", pady=3)
        else:
            ttk.Entry(form, textvariable=v[key], width=40).grid(row=r, column=1, sticky="ew", pady=3)
    err = tk.StringVar()
    ttk.Label(form, textvariable=err, foreground=COLORS['warning'], wraplength=380,
              justify="left").grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(6, 0))
    ttk.Label(form, text="Offsets are in the parent's frame. In offset mode, click the preview to place it.",
              foreground=COLORS['text_muted'], font=("Segoe UI", 8)).grid(
        row=len(fields) + 1, column=0, columnspan=2, sticky="w")

    def read() -> dict:
        out = dict(sub)
        out.update({k: v[k].get().strip() for k in ("ref", "mode", "pads", "comment", "footprint", "lcsc")})
        if not out["ref"]:
            raise ValueError("Reference is required.")
        try:
            out["rotation_offset"] = float(v["rot"].get() or 0)
            out["xy_offset_mm"] = [float(v["dx"].get() or 0), float(v["dy"].get() or 0)]
        except ValueError:
            raise ValueError("Rotation and offsets must be numbers.")
        if out["rotation_offset"].is_integer():
            out["rotation_offset"] = int(out["rotation_offset"])
        if out["mode"] == "pads":
            if not parse_pad_spec(out["pads"]):
                raise ValueError("List the pads this part sits on, or switch to offset mode.")
        return out

    def live(*_a):
        try:
            entry["subparts"][index] = read()
            err.set("")
        except ValueError as e:
            err.set(str(e))
            return
        self._mp_changed()

    for var in v.values():
        var.trace_add("write", live)

    def close(restore: bool):
        if restore:
            if is_new:
                del entry["subparts"][index]
            else:
                entry["subparts"][index] = orig
        self._mp_edit = None
        dlg.destroy()
        self._mp_changed()

    def ok():
        try:
            new = read()
        except ValueError as e:
            err.set(str(e))
            return
        others = {s["ref"] for p in self.mp_config["parents"] for i, s in enumerate(p["subparts"])
                  if not (p is entry and i == index)}
        if new["ref"] in others:
            err.set(f"{new['ref']} is already a sub-part.")
            return
        if new["ref"] == entry["ref"]:
            err.set("A sub-part can't use the parent's reference.")
            return
        entry["subparts"][index] = new
        close(restore=False)

    btns = ttk.Frame(dlg)
    btns.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(btns, text="Cancel", command=lambda: close(restore=True)).pack(side="right", padx=4)
    ttk.Button(btns, text="OK", command=ok, style="Accent.TButton").pack(side="right", padx=4)
    dlg.protocol("WM_DELETE_WINDOW", lambda: close(restore=True))
    self._mp_edit = {"dlg": dlg, "vars": v}


def _mp_run_preview(self):
    """Dry run: compute the patched CSVs and show what would change."""
    if not self.mp_fps:
        messagebox.showwarning("No Project", "Load a project first.")
        return
    config = self._mp_effective_config()
    if not config["parents"]:
        messagebox.showwarning("Nothing to Do", "Add sub-parts to a parent footprint first.")
        return
    placements, warnings, _ghosts = self._mp_compute()
    if not placements:
        messagebox.showerror("Can't Place Sub-parts", "\n".join(warnings))
        return
    self._mp_refresh_subparts()
    files = {}
    try:
        for key in ("cpl", "bom"):
            path = self.mp_paths[key]
            if not path.exists():
                continue
            header, rows = read_csv_rows(path)
            if key == "cpl":
                new_rows, diff = patch_cpl(header, rows, config, placements)
            else:
                new_rows, diff = patch_bom(header, rows, config)
            files[key] = {"path": path, "mtime": path.stat().st_mtime, "header": header,
                          "rows": new_rows, "diff": diff}
    except (OSError, ValueError) as e:
        messagebox.showerror("CSV Error", str(e))
        return
    if "cpl" not in files:
        messagebox.showerror("No CPL", f"Can't find the plugin's CPL:\n{self.mp_paths['cpl']}\n\n"
                                       "Generate the fabrication files with the JLCPCB plugin first.")
        return

    for iid in self.mp_diff_tree.get_children():
        self.mp_diff_tree.delete(iid)
    order = {"removed": 0, "replaced": 1, "changed": 1, "added": 2, "unchanged": 3}
    for key in ("cpl", "bom"):
        for d in sorted(files.get(key, {}).get("diff", []), key=lambda d: order[d["kind"]]):
            self.mp_diff_tree.insert("", "end", tags=(d["kind"],), values=(
                key.upper(), d["kind"], d["ref"],
                ", ".join(d["old"]) if d["old"] else "", ", ".join(d["new"]) if d["new"] else ""))
    self.mp_preview = files
    pending = any(d["kind"] != "unchanged" for f in files.values() for d in f["diff"])
    self.mp_patch_btn.configure(state="normal" if pending else "disabled")
    if "bom" not in files:
        self.mp_warn_var.set((self.mp_warn_var.get() + "\n" if self.mp_warn_var.get() else "")
                             + f"⚠ No BOM found ({self.mp_paths['bom'].name}); only the CPL will be patched.")


def _mp_patch(self):
    """Write the previewed CSVs (with .bak) and save the config."""
    files = self.mp_preview
    if not files:
        messagebox.showwarning("Preview First", "Run Preview before patching.")
        return
    for f in files.values():
        if not f["path"].exists() or f["path"].stat().st_mtime != f["mtime"]:
            self._mp_changed()
            messagebox.showwarning("CSV Changed", f"{f['path'].name} changed since the Preview "
                                                  "(did the plugin run again?). Run Preview again.")
            return
    counts = {}
    for f in files.values():
        for d in f["diff"]:
            if d["kind"] != "unchanged":
                counts[d["kind"]] = counts.get(d["kind"], 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in counts.items())
    names = "\n".join(f"  {f['path'].name}" for f in files.values())
    if not messagebox.askyesno("Patch CSVs?",
                               f"Write these files ({summary})?\n\n{names}\n\n"
                               f"Each is copied to <file>.csv.bak first. "
                               f"The config is saved too."):
        return
    written = []
    try:
        for f in files.values():
            if write_csv_with_backup(f["path"], f["header"], f["rows"]):
                written.append(f["path"].name)
        cfg = multipart_config_save(Path(self.mp_project.get()), self._mp_effective_config())
    except OSError as e:
        messagebox.showerror("Write Failed", str(e))
        return
    self._mp_load_project(Path(self.mp_project.get()), keep_config=True)
    self._mp_run_preview()
    messagebox.showinfo("Patched", "Wrote " + (", ".join(written) or "nothing (already patched)")
                        + f"\nSaved {cfg.name}")


def _mp_save_config(self):
    if not self.mp_project.get():
        messagebox.showwarning("No Project", "Load a project first.")
        return
    try:
        path = multipart_config_save(Path(self.mp_project.get()), self._mp_effective_config())
    except OSError as e:
        messagebox.showerror("Save Failed", str(e))
        return
    messagebox.showinfo("Config Saved", f"Saved {path}")


App._create_multipart_tab   = _create_multipart_tab
App._mp_browse_project      = _mp_browse_project
App._mp_reload              = _mp_reload
App._mp_load_project        = _mp_load_project
App._mp_select_parent       = _mp_select_parent
App._mp_current_ref         = _mp_current_ref
App._mp_parent_entry        = _mp_parent_entry
App._mp_on_parent_change    = _mp_on_parent_change
App._mp_on_drop_toggle      = _mp_on_drop_toggle
App._mp_refresh_presets     = _mp_refresh_presets
App._mp_effective_config    = _mp_effective_config
App._mp_compute             = _mp_compute
App._mp_changed             = _mp_changed
App._mp_refresh_subparts    = _mp_refresh_subparts
App._mp_board_to_canvas     = _mp_board_to_canvas
App._mp_redraw              = _mp_redraw
App._mp_canvas_click        = _mp_canvas_click
App._mp_apply_preset        = _mp_apply_preset
App._mp_save_preset         = _mp_save_preset
App._mp_add_subpart         = _mp_add_subpart
App._mp_remove_subpart      = _mp_remove_subpart
App._mp_edit_subpart        = _mp_edit_subpart
App._mp_run_preview         = _mp_run_preview
App._mp_patch               = _mp_patch
App._mp_save_config         = _mp_save_config


# ═══════════════════════════════════════════════════════════════════════════
# INBOX TAB — Downloads → zips → import → copy symbol names
# ═══════════════════════════════════════════════════════════════════════════

_INBOX_NEW_SECONDS = 15 * 60      # downloads newer than this are highlighted
_INBOX_POLL_MS = 2000


def _open_in_file_manager(path: Path, select: bool = False):
    """Open a folder (or reveal a file) in Explorer / Finder / the desktop's
    file manager."""
    path = Path(path)
    if os.name == "nt":
        if select and path.exists():
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


def _send_to_trash(path: Path) -> bool:
    """Move a file to the Recycle Bin / Trash. Returns False if that isn't
    possible on this platform (caller can then offer a permanent delete)."""
    path = Path(path)
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                            ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                            ("fFlags", ctypes.c_uint16),
                            ("fAnyOperationsAborted", wintypes.BOOL),
                            ("hNameMappings", ctypes.c_void_p),
                            ("lpszProgressTitle", wintypes.LPCWSTR)]
            FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION = 0x3, 0x4, 0x10
            FOF_ALLOWUNDO, FOF_NOERRORUI = 0x40, 0x400
            op = SHFILEOPSTRUCTW()
            op.wFunc = FO_DELETE
            op.pFrom = str(path.resolve()) + "\0"   # must be double-NUL terminated
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
            rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            return rc == 0 and not op.fAnyOperationsAborted and not path.exists()
        if sys.platform == "darwin":
            p = str(path.resolve()).replace('"', '\\"')
            r = subprocess.run(["osascript", "-e",
                                f'tell application "Finder" to delete POSIX file "{p}"'],
                               capture_output=True)
            return r.returncode == 0
        r = subprocess.run(["gio", "trash", str(path)], capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def _detect_sym_nickname(personal_root: Path, sym_name: str = "symbols.kicad_sym"):
    """Symbol-library counterpart of _detect_fp_nickname(): find the nickname
    registered in sym-lib-table for this .kicad_sym file; fall back to the
    file stem (KiCad's default when adding a library)."""
    stem = sym_name[:-len(".kicad_sym")] if sym_name.lower().endswith(".kicad_sym") else sym_name
    if not personal_root:
        return stem or None
    try:
        target = (Path(personal_root) / sym_name).resolve()
    except Exception:
        return stem or None
    vars_map = _kicad_path_vars(Path(personal_root))
    target_norm = os.path.normcase(os.path.normpath(str(target)))
    tables = [d / "sym-lib-table" for d in _all_kicad_config_dirs()]
    tables.append(Path(personal_root) / "sym-lib-table")
    tables.append(Path(personal_root).parent / "sym-lib-table")
    for tbl in tables:
        try:
            if not tbl.exists():
                continue
            txt = tbl.read_text(encoding="utf-8", errors="ignore")
            names = re.findall(r'\(name\s+"([^"]*)"\)', txt)
            uris = re.findall(r'\(uri\s+"([^"]*)"\)', txt)
            for name, uri in zip(names, uris):
                cand = _expand_kicad_uri(uri, vars_map)
                try:
                    cp = Path(cand)
                    if cp.exists() and cp.resolve().samefile(target):
                        return name
                except Exception:
                    pass
                if os.path.normcase(os.path.normpath(cand)) == target_norm:
                    return name
        except Exception:
            pass
    return stem or None


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def _fmt_mtime(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    if dt.date() == now.date():
        return f"Today {dt:%H:%M}"
    if (now.date() - dt.date()).days == 1:
        return f"Yesterday {dt:%H:%M}"
    return f"{dt:%Y-%m-%d %H:%M}"


def _inbox_list_dir(folder: Path, zips_only: bool):
    """[(name, size, mtime)] for regular files in folder, newest first."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                    if zips_only and not e.name.lower().endswith(".zip"):
                        continue
                    st = e.stat()
                    out.append((e.name, st.st_size, st.st_mtime))
                except OSError:
                    pass
    except OSError:
        return []
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _create_inbox_tab(self, parent):
    """Downloads → zips mover, one-click import, and copyable symbol names."""
    parent.grid_columnconfigure(0, weight=1)
    parent.grid_rowconfigure(1, weight=2)
    parent.grid_rowconfigure(3, weight=3)

    self._inbox_sig = {"dl": None, "zip": None}

    # ── Folders ──────────────────────────────────────────────────────────
    lf = ttk.Labelframe(parent, text="Folders", padding=12)
    lf.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
    lf.grid_columnconfigure(1, weight=1)

    ttk.Label(lf, text="Downloads:").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
    ttk.Entry(lf, textvariable=self.downloads_dir).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    ttk.Button(lf, text="📁 Browse", command=self._inbox_pick_downloads).grid(row=0, column=2, padx=2)
    ttk.Button(lf, text="📂 Open", command=lambda: self._inbox_open_dir(self.downloads_dir.get())
               ).grid(row=0, column=3, padx=2)

    ttk.Label(lf, text="Zips (import source):").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
    ttk.Entry(lf, textvariable=self.source).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
    ttk.Button(lf, text="📁 Browse", command=self.pick_source).grid(row=1, column=2, padx=2)
    ttk.Button(lf, text="📂 Open", command=lambda: self._inbox_open_dir(self.source.get())
               ).grid(row=1, column=3, padx=2)

    # ── Two file lists ───────────────────────────────────────────────────
    lists = ttk.Frame(parent)
    lists.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
    lists.grid_columnconfigure(0, weight=1, uniform="lists")
    lists.grid_columnconfigure(1, weight=0)
    lists.grid_columnconfigure(2, weight=1, uniform="lists")
    lists.grid_rowconfigure(0, weight=1)

    def make_tree(container, title, extra_cols=()):
        f = ttk.Labelframe(container, text=title, padding=8)
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=1)
        cols = ("name", "size", "modified") + tuple(c for c, _, _ in extra_cols)
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended", height=8)
        tv.heading("name", text="File"); tv.column("name", width=260, anchor="w")
        tv.heading("size", text="Size"); tv.column("size", width=70, anchor="e", stretch=False)
        tv.heading("modified", text="Modified"); tv.column("modified", width=120, anchor="w", stretch=False)
        for c, label, w in extra_cols:
            tv.heading(c, text=label); tv.column(c, width=w, anchor="center", stretch=False)
        tv.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
        sb.grid(row=0, column=1, sticky="ns")
        tv.configure(yscrollcommand=sb.set)
        tv.tag_configure("fresh", foreground=COLORS['success'])
        tv.tag_configure("imported", foreground=COLORS['text_muted'])
        tv.tag_configure("new", foreground=COLORS['success'])
        return f, tv

    dl_frame, self.inbox_dl_tree = make_tree(lists, "⬇ Downloads")
    dl_frame.grid(row=0, column=0, sticky="nsew")
    zip_frame, self.inbox_zip_tree = make_tree(lists, "🗜 Zips", extra_cols=(("status", "Status", 90),))
    zip_frame.grid(row=0, column=2, sticky="nsew")

    mid = ttk.Frame(lists)
    mid.grid(row=0, column=1, padx=8)
    ttk.Button(mid, text="Move →", style="Accent.TButton",
               command=self._inbox_move_to_zips).pack(fill="x", pady=4)
    ttk.Button(mid, text="↩ Back", command=self._inbox_move_back).pack(fill="x", pady=4)

    dl_btns = ttk.Frame(dl_frame)
    dl_btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Button(dl_btns, text="🗑 Delete", command=self._inbox_delete_downloads).pack(side="left", padx=2)
    ttk.Button(dl_btns, text="↻", width=3, command=lambda: self._inbox_refresh(force=True)
               ).pack(side="left", padx=2)
    ttk.Checkbutton(dl_btns, text="Zips only", variable=self.inbox_zips_only,
                    command=lambda: (self.save_prefs(), self._inbox_refresh(force=True))
                    ).pack(side="left", padx=(8, 0))
    ttk.Label(dl_btns, text="green = last 15 min", foreground=COLORS['text_muted']
              ).pack(side="right")

    zip_btns = ttk.Frame(zip_frame)
    zip_btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Button(zip_btns, text="  ▶️ Import Zips  ", style="Accent.TButton",
               command=self._inbox_run_import).pack(side="left", padx=2)
    ttk.Button(zip_btns, text="↻", width=3, command=lambda: self._inbox_refresh(force=True)
               ).pack(side="left", padx=2)
    ttk.Label(zip_btns, text="Uses Import-tab options", foreground=COLORS['text_muted']
              ).pack(side="right")

    # Double-click acts only on a row (not a column header or empty space)
    def on_row(tree, fn):
        return lambda e: fn() if tree.identify_row(e.y) else None
    self.inbox_dl_tree.bind("<Double-1>", on_row(self.inbox_dl_tree, self._inbox_move_to_zips))
    self.inbox_dl_tree.bind("<Delete>", lambda e: self._inbox_delete_downloads())
    self.inbox_zip_tree.bind("<Double-1>", on_row(self.inbox_zip_tree, self._inbox_reveal_zip))

    # ── Status line ──────────────────────────────────────────────────────
    self.inbox_status = tk.StringVar(value="Move new part zips over, then Import Zips.")
    ttk.Label(parent, textvariable=self.inbox_status, foreground=COLORS['text_muted']
              ).grid(row=2, column=0, sticky="w", padx=20, pady=(4, 0))

    # ── Symbols from the last run ────────────────────────────────────────
    rf = ttk.Labelframe(parent, text="Symbols from last import  —  double-click or Ctrl+C to copy name",
                        padding=8)
    rf.grid(row=3, column=0, sticky="nsew", padx=16, pady=(6, 16))
    rf.grid_columnconfigure(0, weight=1)
    rf.grid_rowconfigure(0, weight=1)

    cols = ("symbol", "footprint", "change", "package")
    tv = ttk.Treeview(rf, columns=cols, show="headings", selectmode="extended", height=8)
    tv.heading("symbol", text="Symbol"); tv.column("symbol", width=260, anchor="w")
    tv.heading("footprint", text="Footprint"); tv.column("footprint", width=320, anchor="w")
    tv.heading("change", text="Result"); tv.column("change", width=200, anchor="w")
    tv.heading("package", text="Package"); tv.column("package", width=240, anchor="w")
    tv.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(rf, orient="vertical", command=tv.yview)
    sb.grid(row=0, column=1, sticky="ns")
    tv.configure(yscrollcommand=sb.set)
    tv.tag_configure("new", foreground=COLORS['success'])
    tv.tag_configure("warn", foreground=COLORS['warning'])
    tv.tag_configure("existing", foreground=COLORS['text'])
    tv.tag_configure("err", foreground=COLORS['error'])
    self.inbox_sym_tree = tv

    tv.bind("<Double-1>", lambda e: self._inbox_copy("symbol") if tv.identify_row(e.y) else None)
    tv.bind("<Control-c>", lambda e: self._inbox_copy("symbol"))
    menu = tk.Menu(self, tearoff=0, bg=COLORS['panel'], fg=COLORS['text'],
                   activebackground=COLORS['accent'], activeforeground='white')
    menu.add_command(label="Copy Symbol Name", command=lambda: self._inbox_copy("symbol"))
    menu.add_command(label="Copy Lib:Symbol", command=lambda: self._inbox_copy("libsym"))
    menu.add_command(label="Copy Footprint", command=lambda: self._inbox_copy("footprint"))

    def popup(e):
        row = tv.identify_row(e.y)
        if row and row not in tv.selection():
            tv.selection_set(row)
        if row:
            menu.post(e.x_root, e.y_root)
    tv.bind("<Button-3>", popup)

    rb = ttk.Frame(rf)
    rb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Button(rb, text="📋 Copy Symbol", command=lambda: self._inbox_copy("symbol")).pack(side="left", padx=2)
    ttk.Button(rb, text="📋 Copy Lib:Symbol", command=lambda: self._inbox_copy("libsym")).pack(side="left", padx=2)
    ttk.Button(rb, text="📋 Copy Footprint", command=lambda: self._inbox_copy("footprint")).pack(side="left", padx=2)
    ttk.Button(rb, text="📋 Copy All Names", command=lambda: self._inbox_copy("symbol", all_rows=True)
               ).pack(side="left", padx=2)
    ttk.Button(rb, text="📄 Full Summary…", command=self._inbox_full_summary).pack(side="right", padx=2)

    # Re-list the zips pane when the source path changes (typed or browsed)
    try:
        self.source.trace_add("write", lambda *a: self.after_idle(lambda: self._inbox_refresh(force=True)))
    except Exception:
        pass


# ─── File lists ──────────────────────────────────────────────────────────
def _inbox_is_visible(self) -> bool:
    try:
        return "inbox" in (self.notebook.tab(self.notebook.select(), "text") or "").lower()
    except Exception:
        return False


def _inbox_poll(self):
    """Cheap 2 s poll; only touches the widgets when a listing changed."""
    try:
        if self._inbox_is_visible():
            self._inbox_refresh()
    except Exception:
        pass
    self.after(_INBOX_POLL_MS, self._inbox_poll)


def _inbox_fill(tree, rows, sig_key, sig_store, row_builder):
    sig = tuple(rows)
    if sig_store.get(sig_key) == sig:
        return False
    sig_store[sig_key] = sig
    selected = set(tree.selection())
    top = tree.yview()[0]
    tree.delete(*tree.get_children())
    for r in rows:
        values, tags = row_builder(r)
        tree.insert("", "end", iid=r[0], values=values, tags=tags)
    keep = [i for i in selected if tree.exists(i)]
    if keep:
        tree.selection_set(keep)
    tree.yview_moveto(top)
    return True


def _inbox_refresh(self, force: bool = False):
    if force:
        self._inbox_sig = {"dl": None, "zip": None}
    now = time.time()

    dl = Path(self.downloads_dir.get()).expanduser() if self.downloads_dir.get() else None
    dl_rows = _inbox_list_dir(dl, self.inbox_zips_only.get()) if dl and dl.is_dir() else []

    def dl_row(r):
        name, size, mt = r
        return (name, _fmt_size(size), _fmt_mtime(mt)), (("fresh",) if now - mt < _INBOX_NEW_SECONDS else ())
    _inbox_fill(self.inbox_dl_tree, dl_rows, "dl", self._inbox_sig, dl_row)

    zd = Path(self.source.get()).expanduser() if self.source.get() else None
    zip_rows = _inbox_list_dir(zd, True) if zd and zd.is_dir() else []
    # Fold the import-log state into the signature so status flips after an import
    log_obj = ImportLog(Path(self.personal.get()).expanduser()) if self.personal.get() else None
    zip_rows = [(n, s, m, bool(log_obj and log_obj.contains("zip", zd / n))) for n, s, m in zip_rows]

    def zip_row(r):
        name, size, mt, done = r
        return ((name, _fmt_size(size), _fmt_mtime(mt), "✓ Imported" if done else "● New"),
                ("imported",) if done else ("new",))
    _inbox_fill(self.inbox_zip_tree, zip_rows, "zip", self._inbox_sig, zip_row)


def _inbox_pick_downloads(self):
    init = self.downloads_dir.get() or str(Path.home())
    p = filedialog.askdirectory(title="Select Downloads Folder", initialdir=init)
    if p:
        self.downloads_dir.set(p)
        self.save_prefs()
        self._inbox_refresh(force=True)


def _inbox_open_dir(self, path_str: str):
    if not path_str:
        messagebox.showinfo("Open Folder", "No folder set.")
        return
    p = Path(path_str).expanduser()
    if not p.is_dir():
        messagebox.showerror("Open Folder", f"Folder not found:\n{p}")
        return
    try:
        _open_in_file_manager(p)
    except Exception as e:
        messagebox.showerror("Open Folder", str(e))


def _inbox_reveal_zip(self):
    sel = self.inbox_zip_tree.selection()
    if sel and self.source.get():
        try:
            _open_in_file_manager(Path(self.source.get()).expanduser() / sel[0], select=True)
        except Exception as e:
            messagebox.showerror("Show in Folder", str(e))


# ─── Move / delete ───────────────────────────────────────────────────────
def _inbox_move(self, names, src_dir: Path, dst_dir: Path, verb: str):
    moved, skipped, errors = [], [], []
    for name in names:
        src, dst = src_dir / name, dst_dir / name
        try:
            if not src.exists():
                continue
            if dst.exists():
                if not messagebox.askyesno(
                        "File exists",
                        f"'{name}' already exists in\n{dst_dir}\n\nReplace it?", parent=self):
                    skipped.append(name)
                    continue
                dst.unlink()
            shutil.move(str(src), str(dst))
            moved.append(name)
        except Exception as e:
            errors.append(f"{name}: {e}")
    msg = f"{verb} {len(moved)} file(s)"
    if skipped:
        msg += f", skipped {len(skipped)}"
    if len(moved) == 1:
        msg += f": {moved[0]}"
    self.inbox_status.set(msg)
    if errors:
        messagebox.showerror("Move", "\n".join(errors))
    self._inbox_refresh(force=True)
    for tree in (self.inbox_zip_tree, self.inbox_dl_tree):
        keep = [n for n in moved if tree.exists(n)]
        if keep:
            tree.selection_set(keep)
            tree.see(keep[0])
    return moved


def _inbox_move_to_zips(self):
    names = list(self.inbox_dl_tree.selection())
    if not names:
        self.inbox_status.set("Select one or more downloads to move.")
        return
    if not self.source.get():
        messagebox.showerror("Move", "Set the Zips folder first.")
        return
    dl = Path(self.downloads_dir.get()).expanduser()
    zd = Path(self.source.get()).expanduser()
    if not zd.exists():
        if not messagebox.askyesno("Create folder?", f"Zips folder doesn't exist:\n{zd}\n\nCreate it?"):
            return
        zd.mkdir(parents=True, exist_ok=True)
    self._inbox_move(names, dl, zd, "Moved")


def _inbox_move_back(self):
    names = list(self.inbox_zip_tree.selection())
    if not names:
        self.inbox_status.set("Select one or more zips to move back to Downloads.")
        return
    dl = Path(self.downloads_dir.get()).expanduser()
    if not dl.is_dir():
        messagebox.showerror("Move", f"Downloads folder not found:\n{dl}")
        return
    self._inbox_move(names, Path(self.source.get()).expanduser(), dl, "Moved back")


def _inbox_delete_downloads(self):
    names = list(self.inbox_dl_tree.selection())
    if not names:
        self.inbox_status.set("Select one or more downloads to delete.")
        return
    dl = Path(self.downloads_dir.get()).expanduser()
    listing = "\n".join(names[:10]) + (f"\n…and {len(names) - 10} more" if len(names) > 10 else "")
    if not messagebox.askyesno("Delete", f"Send {len(names)} file(s) to the Recycle Bin?\n\n{listing}"):
        return
    trashed, failed = 0, []
    for n in names:
        if _send_to_trash(dl / n):
            trashed += 1
        else:
            failed.append(n)
    if failed and messagebox.askyesno(
            "Recycle Bin unavailable",
            f"{len(failed)} file(s) couldn't be sent to the Recycle Bin.\n"
            "Delete them permanently instead?"):
        for n in list(failed):
            try:
                (dl / n).unlink()
                trashed += 1
                failed.remove(n)
            except Exception:
                pass
    self.inbox_status.set(f"Deleted {trashed} file(s)" + (f", {len(failed)} failed" if failed else ""))
    self._inbox_refresh(force=True)


# ─── Import + symbol list ────────────────────────────────────────────────
def _inbox_run_import(self):
    zd = Path(self.source.get()).expanduser() if self.source.get() else None
    if not zd or not zd.is_dir():
        messagebox.showerror("Import", "Zips folder not found. Set it at the top of this tab.")
        return
    self.inbox_status.set("⚙️ Importing…")
    self.update_idletasks()
    self.run(inbox=True)
    self._inbox_refresh(force=True)


def _inbox_show_results(self, results, total, all_pkgs, skipped_already):
    tv = self.inbox_sym_tree
    tv.delete(*tv.get_children())

    # Footprint for each symbol, read back from the library just written
    fp_by_name = {}
    try:
        for name, block in list_symbols(self.current_sym_path()):
            m = re.search(r'\(\s*property\s+"Footprint"\s+"([^"]*)"', block)
            fp_by_name[name] = m.group(1) if m else ""
    except Exception:
        pass

    labels = {"ADD": ("New", "new"), "RENAME": (None, "warn"), "REPLACE": ("Replaced existing", "warn"),
              "SKIP_DUP": (None, "existing"), "SKIP_EXISTS": ("Already in library (same name)", "existing")}
    n = 0
    seen = set()
    for r in results:
        pkg = Path(r.get("package", "")).name
        for orig, action, target in r.get("symbol_names_debug", []):
            if action == "RENAME":
                text = f"Renamed (from {orig})"
            elif action == "SKIP_DUP":
                text = "Already in library" if orig == target else f"Already in library as {target}"
            else:
                text = labels.get(action, (action, "existing"))[0]
            tag = labels.get(action, (None, "existing"))[1]
            key = (target, pkg)
            if key in seen:
                continue
            seen.add(key)
            n += 1
            tv.insert("", "end", iid=f"r{n}", tags=(tag,),
                      values=(target, fp_by_name.get(target, ""), text, pkg))
        for e in r.get("errors", []):
            n += 1
            tv.insert("", "end", iid=f"r{n}", tags=("err",), values=("—", "", f"ERROR: {e}", pkg))

    new = sum(len(r.get("symbols_added_names", [])) for r in results)
    msg = f"Processed {len(results)} zip(s): {new} new symbol(s)"
    if skipped_already:
        msg += f"  ·  {len(skipped_already)} skipped (already imported)"
    if total.get("err"):
        msg += f"  ·  ⚠ {total['err']} error(s)"
    if self.dry_run.get():
        msg += "  ·  DRY RUN — nothing written"
    if not results:
        msg = ("Nothing new to import — every zip is already in the import log." if skipped_already
               else "No packages found in the zips folder.")
    self.inbox_status.set(msg)

    kids = tv.get_children()
    if kids:
        tv.selection_set(kids[0])
        tv.focus(kids[0])
        tv.focus_set()


def _inbox_copy(self, what: str, all_rows: bool = False):
    tv = self.inbox_sym_tree
    rows = tv.get_children() if all_rows else tv.selection()
    items = []
    lib = None
    for iid in rows:
        sym, fp, _change, _pkg = tv.item(iid, "values")
        if sym in ("", "—"):
            continue
        if what == "footprint":
            val = fp
        elif what == "libsym":
            if lib is None:
                lib = _detect_sym_nickname(Path(self.personal.get()).expanduser(),
                                           self.sym_choice.get()) or ""
            val = f"{lib}:{sym}" if lib else sym
        else:
            val = sym
        if val and val not in items:
            items.append(val)
    if not items:
        self.inbox_status.set("Nothing selected to copy.")
        return "break"
    text = "\n".join(items)
    self.clipboard_clear()
    self.clipboard_append(text)
    self.update()   # keeps the clipboard content after focus moves to KiCad
    self.inbox_status.set(f"📋 Copied: {items[0]}" if len(items) == 1 else f"📋 Copied {len(items)} names")
    return "break"


def _inbox_full_summary(self):
    args = getattr(self, "_last_run_args", None)
    if not args:
        messagebox.showinfo("Summary", "No import has been run yet this session.")
        return
    self._show_import_summary_dialog(*args)


# ─── Bind inbox methods to App class ───────────────────────────────────────
App._create_inbox_tab        = _create_inbox_tab
App._inbox_is_visible        = _inbox_is_visible
App._inbox_poll              = _inbox_poll
App._inbox_refresh           = _inbox_refresh
App._inbox_pick_downloads    = _inbox_pick_downloads
App._inbox_open_dir          = _inbox_open_dir
App._inbox_reveal_zip        = _inbox_reveal_zip
App._inbox_move              = _inbox_move
App._inbox_move_to_zips      = _inbox_move_to_zips
App._inbox_move_back         = _inbox_move_back
App._inbox_delete_downloads  = _inbox_delete_downloads
App._inbox_run_import        = _inbox_run_import
App._inbox_show_results      = _inbox_show_results
App._inbox_copy              = _inbox_copy
App._inbox_full_summary      = _inbox_full_summary


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()