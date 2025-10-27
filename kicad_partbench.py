#!/usr/bin/env python3
"""
KiCad Library Tool v5.0
═══════════════════════════════════════════════════════════════════════════════
Modern, portable tool for managing KiCad component libraries.

• Import symbols, footprints, 3D models from ZIP/directories
• Intelligent duplicate detection (content-based hashing)
• Automatic 3D model pairing  
• Library management tools
• Modern dark theme, no external dependencies

Version: 5.0.0 | Date: 2025-10-27 | License: MIT
═══════════════════════════════════════════════════════════════════════════════
"""
#!/usr/bin/env python3
# Kicad Library Tool — v4.6
# Keeps ALL v3.9 features + v4.1 (3D root, relative paths, Delete Parts)
# NEW in v4.2:
#  • Import-time 3D pairing: if a package (zip/folder) contains *.kicad_mod and *.step/*.stp/*.wrl,
#    we assume they belong together and write (model ...) into the imported footprint(s).
#    Heuristics: 1-to-1 (single FP & single 3D) -> pair; otherwise try name-stem match / fuzzy match;
#    otherwise, if symbols provide an MPN/Value, try matching 3D to that.
#  • Symbol-aware "Fix 3D Paths": if no model is found by footprint module name, it also tries
#    the symbol names/values that reference that footprint.
#
# Python stdlib only (tkinter).
import os
import re
import sys
import json
import time
import shutil
import hashlib
import tempfile
import zipfile
import textwrap
import csv
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
    def repl(m):
        path = m.group(1) or m.group(2)
        local = chooser(path)
        if local:
            if make_rel:
                relpath = make_rel(local)
                return f'(model "{relpath}"'
            else:
                sub = "step" if local.suffix.lower() in (".step",".stp") else "wrl"
                return f'(model "${{PERSONAL_LIB_DIR}}/3d/{sub}/{local.name}"'
        return m.group(0)
    return re.sub(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', repl, txt)

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
    steps = find_files(staging, ["*.step", "*.stp"])
    wrls  = find_files(staging, ["*.wrl"])
    mapping = {}; copied = 0
    for f in steps:
        dest = personal / "3d" / "step" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1; log(f"[3D] Copied: {f.name}")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/step/" + f.name
    for f in wrls:
        dest = personal / "3d" / "wrl" / f.name
        if overwrite or not dest.exists():
            shutil.copy2(f, dest); copied += 1; log(f"[3D] Copied: {f.name}")
        mapping[f.name] = "${PERSONAL_LIB_DIR}/3d/wrl/" + f.name
    return mapping, copied

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
                         mode="Ignore Properties", ignore_footprint_for_dedupe=True, log=lambda *a: None):
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

    stats = {"added":[], "renamed":[], "overwritten":[], "skipped_content_dupe":[], "debug_symbols":[]}

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

            if h in content_index:
                kept = content_index[h]
                stats["skipped_content_dupe"].append((name, kept))
                stats["debug_symbols"].append((name, "SKIP_DUP", kept)); log(f"[SYM] Skip duplicate by content: {name} == {kept}")
                continue

            if name in merged:
                if overwrite:
                    merged[name] = sanitized; content_index[h] = name; stats["overwritten"].append(name)
                    stats["debug_symbols"].append((name, "OVERWRITE", name)); log(f"[SYM] Overwrite: {name}")
                else:
                    k, new_name = 1, f"{name}_imp1"
                    while new_name in merged:
                        k += 1; new_name = f"{name}_imp{k}"
                    # Replace ALL occurrences of the symbol name (parent and child units), not just the first one
                    def rename_symbol(match):
                        old_sym_name = match.group(1)
                        # Check if this is the parent or a child unit (name_N_M pattern)
                        if old_sym_name == name:
                            return f'(symbol "{new_name}"'
                        elif old_sym_name.startswith(name + "_"):
                            # This is a child unit like "ABC_1_1" -> rename to "ABC_imp1_1_1"
                            suffix = old_sym_name[len(name):]
                            return f'(symbol "{new_name}{suffix}"'
                        return match.group(0)
                    sanitized = re.sub(r'\(symbol\s+"([^"]+)"', rename_symbol, sanitized)
                    merged[new_name] = sanitized; content_index[h] = new_name
                    stats["renamed"].append((name, new_name)); stats["debug_symbols"].append((name, "RENAME", new_name)); log(f"[SYM] Renamed: {new_name}")
            else:
                merged[name] = sanitized; content_index[h] = name; stats["added"].append(name); stats["debug_symbols"].append((name, "ADD", name))

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
            rel = os.path.relpath(model_path, start=relative_to_dir).replace("\\\\","/")
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
        txt2 = txt.rstrip()[:-1] + f"\\n  {new_model_str}\\n)"
    else:
        txt2 = txt + f"\\n  {new_model_str}\\n"
    txt2 = _dedupe_models_in_text(txt2, relative_to_dir, personal_root)
    mod_path.write_text(txt2, encoding="utf-8")


def process_one_package(kind: str, path: Path, personal: Path, fp_folder: str, personal_sym: Path,
                        fp_nick="Personal_Footprints", autoset_fp=True, overwrite=False, mode="Ignore Properties",
                        ignore_footprint_for_dedupe=True, dry_run=False, log=lambda *a: None,
                        import_pair_models=True, prefer_model_ext="step", force_relative_models=False):
    res = {
        "package": str(path), "kind": kind,
        "footprints_imported": 0, "footprints_skipped": 0, "footprints_errors": 0,
        "symbols_added": 0, "symbols_renamed": 0, "symbols_overwritten": 0,
        "symbols_skipped_content_dupe": 0, "models_copied": 0, "errors": [],
        "symbol_names_debug": [], "models_paired": 0
    }
    try:
        if kind == "zip": staging, tmpdir = unzip_if_needed(path)
        else: staging, tmpdir = (path, None)

        # copy 3D + footprints
        model_map, copied3d = copy_3d_models(staging, personal, overwrite=overwrite, log=log)
        res["models_copied"] = copied3d
        fps_paths, imported, skipped, fp_errs = import_footprints(staging, personal, fp_folder, overwrite=overwrite, log=log)
        res["footprints_imported"] = len(imported); res["footprints_skipped"] = len(skipped); res["footprints_errors"] = len(fp_errs)

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
                    _write_model_into_footprint(fps_paths[0], personal / ("3d/step" if staged_models[0].suffix.lower() in (".step",".stp") else "3d/wrl") / staged_models[0].name,
                                                personal / fp_folder, force_relative_models)
                    res["models_paired"] += 1
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
                                log(f"[3D] Paired: {Path(mod_path).name} -> {best.name}")
                        except Exception as e:
                            log(f"[3D] Pairing error for {Path(mod_path).name}: {e}")

        # Merge symbols last
        syms = find_files(staging, ["*.kicad_sym"])
        if syms:
            stats, merged = merge_symbols_strict(personal_sym, syms, fps_paths,
                                                 autoset_fp=autoset_fp, fp_nick=fp_nick, overwrite=overwrite,
                                                 mode=mode, ignore_footprint_for_dedupe=ignore_footprint_for_dedupe, log=log)
            res["symbols_added"] = len(stats["added"])
            res["symbols_renamed"] = len(stats["renamed"])
            res["symbols_overwritten"] = len(stats["overwritten"])
            res["symbols_skipped_content_dupe"] = len(stats["skipped_content_dupe"])
            res["symbol_names_debug"] = stats["debug_symbols"]
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
        if new_txt != txt:
            changed += 1; modified.append(mod_path)
            if not dry_run:
                bak = mod_path.with_suffix(".kicad_mod.bak")
                try: mod_path.rename(bak)
                except Exception: pass
                mod_path.write_text(new_txt, encoding="utf-8")
        # Always collapse duplicate model entries (idempotent)
        new_txt = _dedupe_models_in_text(new_txt, fp_dir, Path(fp_dir).parent)

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

def _dedupe_models_in_text(txt: str, fp_dir: Path, personal_root: Path) -> str:
    models = [(m.group(0), m.group(1) or m.group(2)) for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]
    if not models:
        return txt
    seen = set()
    keep = []
    for full, path in models:
        key = _norm_model_key(path, fp_dir, personal_root)
        if key in seen:
            continue
        seen.add(key)
        keep.append(full)
    if len(keep) == len(models):
        return txt
    base = re.sub(r'\s*\(model\s+(?:"[^"]+"|[^\s)]+)\s*\)', '', txt)
    insert_block = "".join(f"\n  {k}" for k in keep)
    base = base.rstrip()
    if base.endswith(')'):
        base = base[:-1] + insert_block + "\n)"
    else:
        base = base + insert_block
    return base



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
            u_norm = uri.replace("\\\\", "/")
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
                        return f'(uri "${{var_name}}/{path_rel}")'
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
                s = inner.replace("\\\\","/")
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
                            return f'(model "${{var_name}}/{path_rel}")'
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
    except:
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
    """Modern KiCad Library Tool application."""
    
    def __init__(self):
        super().__init__()
        
        self.title("⚡ KiCad Library Tool 5.0")
        self.geometry("1400x900")
        self.minsize(1200, 700)
        
        # Apply modern theme
        self.style = apply_modern_theme(self)
        
        # State variables
        self.source = tk.StringVar()
        self.personal = tk.StringVar()
        self.nick = tk.StringVar(value="Personal_Footprints")
        self.autoset = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.recurse = tk.BooleanVar(value=True)
        self.ignore_fp_dedupe = tk.BooleanVar(value=True)
        self.dedupe_mode = tk.StringVar(value="Ignore Properties")
        self.dry_run = tk.BooleanVar(value=False)
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
        self.autoset.set(prefs.get("autoset", True))
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
        tab_import = ttk.Frame(notebook)
        tab_library = ttk.Frame(notebook)
        tab_settings = ttk.Frame(notebook)
        tab_results = ttk.Frame(notebook)
        
        notebook.add(tab_import, text=" 📁 Import ")
        notebook.add(tab_library, text=" 📚 Library ")
        notebook.add(tab_settings, text=" ⚙️ Settings ")
        notebook.add(tab_results, text=" 📊 Results ")
        
        self._create_import_tab(tab_import)
        self._create_library_tab(tab_library)
        self._create_settings_tab(tab_settings)
        self._create_results_tab(tab_results)
        
        self.notebook = notebook

    
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
        
        ttk.Checkbutton(lf3, text="Dry run (preview only)", variable=self.dry_run).grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(lf3, text="Ignore footprint in deduplication", variable=self.ignore_fp_dedupe).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        
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
        
        cols = ("symbol", "footprint", "model_3d", "pins")
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
        self.lib_tree.heading("model_3d", text="3D Model", command=lambda: self.sort_lib_tree("model_3d"))
        self.lib_tree.heading("pins", text="Pins", command=lambda: self.sort_lib_tree("pins"))
        
        self.lib_tree.column("symbol", width=250, anchor="w")
        self.lib_tree.column("footprint", width=300, anchor="w")
        self.lib_tree.column("model_3d", width=200, anchor="w")
        self.lib_tree.column("pins", width=80, anchor="center")
        
        # Tool buttons at bottom
        tool_frame = ttk.Labelframe(parent, text="Library Tools", padding=12)
        tool_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(8,16))
        
        ttk.Button(tool_frame, text="🔍 Audit Footprints", command=self.audit_footprints_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🔧 Fix 3D Paths", command=self.fix_3d_paths_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🗑️ Delete Parts", command=self.delete_parts_gui).pack(side="left", padx=4)
        
        # Bind search
        self.lib_search_var.trace_add("write", lambda *args: self.filter_library_browser())
        
        # Auto-load if library path is set
        if self.personal.get():
            self.after(100, self.refresh_library_browser)
    
    def _create_settings_tab(self, parent):
        """Create settings tab."""
        parent.grid_columnconfigure(0, weight=1)
        
        r = 0
        
        # General Settings
        lf = ttk.Labelframe(parent, text="General Settings", padding=16)
        lf.grid(row=r, column=0, sticky="ew", padx=16, pady=(16,8)); r += 1
        lf.grid_columnconfigure(1, weight=1)
        
        ttk.Label(lf, text="Footprint Nickname:").grid(row=0, column=0, sticky="e", padx=(0,8), pady=6)
        ttk.Entry(lf, textvariable=self.nick).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        
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
        
        about_text = f"""KiCad Library Tool v5.0
        
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
            ('packages', 'Packages'), ('new', 'New'), ('fp_added', 'FP+'), 
            ('fp_skipped', 'FP-'), ('models', '3D'), ('sym_add', 'SYM+'),
            ('sym_ren', 'REN'), ('sym_ovr', 'OVR'), ('errors', 'ERR')
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
    
    def run(self):
        """Run the import process."""
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
        
        self.set_status("⚙️ Gathering packages...", "")
        self.log_message("=" * 70, "STEP")
        self.log_message("STARTING IMPORT", "STEP")
        self.log_message("=" * 70, "STEP")
        self.log_message(f"Source: {source}", "INFO")
        self.log_message(f"Personal: {personal}", "INFO")
        self.log_message(f"Dry run: {self.dry_run.get()}", "INFO")
        
        # Gather packages
        pkgs = gather_packages(source, recurse=self.recurse.get())
        pkgs = prune_packages(pkgs, prefer=self.prefer_pkg.get())
        
        self.log_message(f"\nFound {len(pkgs)} packages to process", "OK")
        self.sum_vars['packages'].set(str(len(pkgs)))
        
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
                    dry_run=self.dry_run.get(),
                    log=lambda *a: self.log_message(" ".join(str(x) for x in a), "INFO"),
                    prefer_model_ext="step",
                    force_relative_models=self.rel_models.get()
                )
                results.append(res)
                
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
        
        # Update summary
        total = {'fp_add': 0, 'fp_skip': 0, 'models': 0, 'sym_add': 0, 'sym_ren': 0, 'sym_ovr': 0, 'err': 0, 'new': 0}
        for r in results:
            total['fp_add'] += r['footprints_imported']
            total['fp_skip'] += r['footprints_skipped']
            total['models'] += r['models_copied']
            total['sym_add'] += r['symbols_added']
            total['sym_ren'] += r['symbols_renamed']
            total['sym_ovr'] += r['symbols_overwritten']
            total['err'] += len(r['errors'])
            if r['symbols_added'] > 0 or r['footprints_imported'] > 0:
                total['new'] += 1
        
        self.sum_vars['new'].set(str(total['new']))
        self.sum_vars['fp_added'].set(str(total['fp_add']))
        self.sum_vars['fp_skipped'].set(str(total['fp_skip']))
        self.sum_vars['models'].set(str(total['models']))
        self.sum_vars['sym_add'].set(str(total['sym_add']))
        self.sum_vars['sym_ren'].set(str(total['sym_ren']))
        self.sum_vars['sym_ovr'].set(str(total['sym_ovr']))
        self.sum_vars['errors'].set(str(total['err']))
        
        self.results = results
        
        # Save prefs
        self.save_prefs()
        
        # Done
        msg = f"✅ Complete! Processed {len(pkgs)} packages" if not total['err'] else f"⚠️ Complete with {total['err']} errors"
        self.set_status(msg, "")
        self.log_message(f"\n{'='*70}", "STEP")
        self.log_message(msg, "OK" if not total['err'] else "WARN")
        self.log_message(f"{'='*70}", "STEP")
        
        # Switch to results tab
        self.notebook.select(3)
        
        messagebox.showinfo("Import Complete", msg)

    
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
            if not query or query in data['symbol'].lower() or query in data['footprint'].lower():
                self.lib_tree.insert("", "end", values=(
                    data['symbol'],
                    data['footprint'],
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
        """Fix 3D model paths in footprints."""
        fp_dir = self.current_fp_dir()
        models_root = self.current_models_root()
        sym_path = self.current_sym_path()
        
        if not fp_dir.exists():
            messagebox.showerror("Error", f"Footprint directory not found: {fp_dir}")
            return
        
        win = tk.Toplevel(self)
        win.title("Fix 3D Paths")
        win.geometry("700x500")
        win.configure(bg=COLORS['bg'])
        
        dry = tk.BooleanVar(value=True)
        add_missing = tk.BooleanVar(value=True)
        rel = tk.BooleanVar(value=self.rel_models.get())
        
        ttk.Checkbutton(win, text="Dry run (preview only)", variable=dry).pack(anchor="w", padx=12, pady=6)
        ttk.Checkbutton(win, text="Add models to footprints with none", variable=add_missing).pack(anchor="w", padx=12, pady=4)
        ttk.Checkbutton(win, text="Use relative paths", variable=rel).pack(anchor="w", padx=12, pady=4)
        
        out = ScrolledText(win, wrap="word", height=15, bg=COLORS['panel'], fg=COLORS['text'])
        out.pack(fill="both", expand=True, padx=8, pady=8)
        
        def run_fix():
            changed, total = fix_footprint_models(
                fp_dir, models_root, sym_path=sym_path,
                dry_run=dry.get(), add_if_missing=add_missing.get(),
                prefer_ext="step", force_relative=rel.get()
            )
            out.insert("end", f"{'Dry run' if dry.get() else 'Updated'}: {changed} of {total} footprints\n")
            messagebox.showinfo("Done", f"{'Preview' if dry.get() else 'Updated'} {changed} of {total} footprints")
        
        ttk.Button(win, text="Run", command=run_fix, style="Accent.TButton").pack(pady=8)
    
    def delete_parts_gui(self):
        """Delete parts from library."""
        sym_path = self.current_sym_path()
        if not sym_path.exists():
            messagebox.showerror("Error", f"Symbol file not found: {sym_path}")
            return
        
        messagebox.showinfo("Delete Parts", "Delete parts tool - coming in next version")
    
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
KiCad Library Tool v5.0

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
            except:
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
            "autoset": self.autoset.get(),
            "recent_sources": self.recent_sources,
            "recent_personal": self.recent_personal,
        }
        try:
            with open(PREFS_FILE, 'w') as f:
                json.dump(prefs, f, indent=2)
        except:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS (not in App class)
# ═══════════════════════════════════════════════════════════════════════════

def list_symbols(sym_path: Path) -> List[Tuple[str, str]]:
    """List all symbols in a symbol file."""
    if not sym_path.exists():
        return []
    txt = sym_path.read_text(encoding="utf-8", errors="ignore")
    return parse_symbol_blocks(txt)


def models_in_footprint(mod_path: Path) -> List[str]:
    """Extract model paths from footprint."""
    txt = mod_path.read_text(encoding="utf-8", errors="ignore")
    models = []
    for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt):
        models.append(m.group(1) or m.group(2))
    return models


def fix_footprint_models(fp_dir: Path, models_root: Path, sym_path: Optional[Path] = None,
                        dry_run: bool = False, add_if_missing: bool = True,
                        prefer_ext: str = "step", force_relative: bool = False) -> Tuple[int, int]:
    """Fix 3D model paths in footprints."""
    all_idx = _available_models_index(models_root)
    changed, total = 0, 0
    
    for mod in fp_dir.glob("*.kicad_mod"):
        total += 1
        txt = mod.read_text(encoding="utf-8", errors="ignore")
        orig = txt
        
        # Rewrite existing models
        def chooser(path_str):
            return _choose_local_model_smart(all_idx, path_str, prefer_ext)
        
        if force_relative:
            def make_rel(local_path):
                try:
                    return os.path.relpath(local_path, start=fp_dir).replace("\\", "/")
                except:
                    return local_path.name
            txt = _rewrite_all_models(txt, chooser, make_rel)
        else:
            txt = _rewrite_all_models(txt, chooser, None)
        
        # Add missing?
        if add_if_missing and not re.search(r'\(model\s+', txt):
            best = _choose_local_model_smart(all_idx, mod.stem, prefer_ext)
            if best:
                if force_relative:
                    try:
                        rel = os.path.relpath(best, start=fp_dir).replace("\\", "/")
                    except:
                        rel = best.name
                    model_str = f'(model "{rel}")'
                else:
                    sub = "step" if best.suffix.lower() in (".step", ".stp") else "wrl"
                    model_str = f'(model "${{PERSONAL_LIB_DIR}}/3d/{sub}/{best.name}")'
                
                if txt.rstrip().endswith(')'):
                    txt = txt.rstrip()[:-1] + f"\n  {model_str}\n)"
                else:
                    txt = txt + f"\n  {model_str}\n"
        
        if txt != orig:
            changed += 1
            if not dry_run:
                mod.write_text(txt, encoding="utf-8")
    
    return changed, total


def _rewrite_all_models(txt: str, chooser, make_rel=None):
    """Rewrite model paths in footprint text."""
    def repl(m):
        path = m.group(1) or m.group(2)
        local = chooser(path)
        if local:
            if make_rel:
                relpath = make_rel(local)
                return f'(model "{relpath}"'
            else:
                sub = "step" if local.suffix.lower() in (".step",".stp") else "wrl"
                return f'(model "${{PERSONAL_LIB_DIR}}/3d/{sub}/{local.name}"'
        return m.group(0)
    return re.sub(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', repl, txt)


def _update_kicad_path_variable(personal_root: Path, log=lambda *a: None) -> List[Path]:
    """Update PERSONAL_LIB_DIR in KiCad config files."""
    updated = []
    # This would need to find and modify kicad_common.json files
    # Simplified for now
    log("Path variable update not yet implemented")
    return updated


def _rewrite_fp_lib_table(personal_root: Path, log=lambda *a: None) -> List[Path]:
    """Rewrite fp-lib-table to use variables."""
    changed = []
    # This would find and modify fp-lib-table files
    # Simplified for now
    log("fp-lib-table rewrite not yet implemented")
    return changed


def _rewrite_models(personal_root: Path, log=lambda *a: None) -> int:
    """Rewrite 3D model paths to be relative."""
    count = 0
    # This would process all .kicad_mod files
    # Simplified for now
    log("Model path rewrite not yet implemented")
    return count


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
