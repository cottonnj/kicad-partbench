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
        tab_bom = ttk.Frame(notebook)
        tab_settings = ttk.Frame(notebook)
        tab_results = ttk.Frame(notebook)
        
        notebook.add(tab_import, text=" 📁 Import ")
        notebook.add(tab_library, text=" 📚 Library ")
        notebook.add(tab_bom, text=" 📋 BOM Manager ")
        notebook.add(tab_settings, text=" ⚙️ Settings ")
        notebook.add(tab_results, text=" 📊 Results ")
        
        self._create_import_tab(tab_import)
        self._create_library_tab(tab_library)
        self._create_bom_manager_tab(tab_bom)
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
        ttk.Button(tool_frame, text="🔧 Repair/Restore Library", command=self.repair_library_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🔍 Audit Footprints", command=self.audit_footprints_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🔧 Fix 3D Paths", command=self.fix_3d_paths_gui).pack(side="left", padx=4)
        ttk.Button(tool_frame, text="🗑️ Delete Parts", command=self.delete_parts_gui).pack(side="left", padx=4)
        
        # Bind search
        self.lib_search_var.trace_add("write", lambda *args: self.filter_library_browser())
        
        # Auto-load if library path is set
        if self.personal.get():
            self.after(100, self.refresh_library_browser)
    
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
                # Find the Reference property line to insert after
                ref_match = re.search(r'(\(property\s+"Reference"[^\)]+\))', block)
                if ref_match:
                    insert_point = ref_match.end()
                    mpn_property = f'\n    (property "MPN" "{found_value}")'
                    block = block[:insert_point] + mpn_property + block[insert_point:]
                else:
                    # Fallback: insert at beginning of symbol
                    symbol_start = re.search(r'(\(symbol\s+"[^"]+"\s+)', block)
                    if symbol_start:
                        insert_point = symbol_start.end()
                        mpn_property = f'\n    (property "MPN" "{found_value}")'
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
    # For CSV, use pandas
    if bom_path.suffix.lower() == '.csv':
        import pandas as pd
        df = pd.read_csv(bom_path)
        data = df.to_dict('records')
        headers = list(df.columns)
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
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
