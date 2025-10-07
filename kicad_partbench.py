#!/usr/bin/env python3
# Kicad Library Tool — v4.5
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
import os, re, shutil, tempfile, zipfile, time, json, csv, textwrap, hashlib
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

HEADER = '(kicad_symbol_lib (version 20211014) (generator "Kicad_Library_Tool_4.5")\n'
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
                    sanitized = re.sub(r'\(symbol\s+"([^"]+)"', f'(symbol "{new_name}"', sanitized, count=1)
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
                        return f'(uri "${{{var_name}}}/{str(rel).replace("\\\\","/")}")'
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
                            return f'(model "${{{var_name}}}/{str(rel).replace("\\\\","/")}")'
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
        os.utime(pretty_dir, None)
    except Exception:
        pass
    return cnt_changed

# -------------------- GUI --------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kicad Library Tool — v4.5")
        self.geometry("1180x880")

        self.source = tk.StringVar(); self.personal = tk.StringVar()
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
        self.force_relative_models = tk.BooleanVar(value=True)

        self.prefs = self.load_prefs()
        if self.prefs.get("last_source_dir"): self.source.set(self.prefs["last_source_dir"])
        if self.prefs.get("last_personal_dir"): self.personal.set(self.prefs["last_personal_dir"])
        if self.prefs.get("sym_choice"): self.sym_choice.set(self.prefs["sym_choice"])
        if self.prefs.get("fp_choice"): self.fp_choice.set(self.prefs["fp_choice"])
        if self.prefs.get("models_root"): self.models_root.set(self.prefs["models_root"])
        if "force_relative_models" in self.prefs: self.force_relative_models.set(self.prefs["force_relative_models"])

        r = 0
        tk.Label(self, text="Source (ZIP, single folder, or a directory containing many packages):").grid(row=r, column=0, columnspan=7, sticky="w", padx=8, pady=(10,4)); r += 1
        tk.Entry(self, textvariable=self.source, width=120).grid(row=r, column=0, columnspan=6, sticky="we", padx=8)
        tk.Button(self, text="Browse…", command=self.pick_source).grid(row=r, column=6, padx=8); r += 1

        tk.Label(self, text="Personal library folder (contains your symbol lib and *.pretty folder):").grid(row=r, column=0, columnspan=7, sticky="w", padx=8, pady=(10,4)); r += 1
        tk.Entry(self, textvariable=self.personal, width=120).grid(row=r, column=0, columnspan=6, sticky="we", padx=8)
        tk.Button(self, text="Browse…", command=self.pick_personal).grid(row=r, column=6, padx=8); r += 1

        tk.Label(self, text="Symbol library file (*.kicad_sym):").grid(row=r, column=0, sticky="e", padx=8)
        self.sym_combo = ttk.Combobox(self, textvariable=self.sym_choice, values=[], width=40); self.sym_combo.grid(row=r, column=1, sticky="w", padx=8)
        tk.Label(self, text="Footprint library folder (*.pretty):").grid(row=r, column=2, sticky="e", padx=8)
        self.fp_combo = ttk.Combobox(self, textvariable=self.fp_choice, values=[], width=40); self.fp_combo.grid(row=r, column=3, sticky="w", padx=8)

        tk.Label(self, text="3D models root:").grid(row=r, column=4, sticky="e", padx=8)
        tk.Entry(self, textvariable=self.models_root, width=28).grid(row=r, column=5, sticky="w", padx=4)
        tk.Button(self, text="Browse…", command=self.pick_models_root).grid(row=r, column=6, padx=8); r += 1
        tk.Checkbutton(self, text="Write RELATIVE (model …) paths (relative to *.pretty)", variable=self.force_relative_models).grid(row=r, column=0, columnspan=7, sticky="w", padx=10); r += 1

        tk.Label(self, text="Footprint nickname (for symbol Footprint property):").grid(row=r, column=0, sticky="e", padx=8, pady=(6,2))
        tk.Entry(self, textvariable=self.nick, width=30).grid(row=r, column=1, sticky="w", padx=8, pady=(6,2))
        tk.Label(self, text="De-duplication mode:").grid(row=r, column=2, sticky="e", padx=8)
        ttk.Combobox(self, textvariable=self.dedupe_mode, values=["Exact","Ignore Properties","Pins-only"]).grid(row=r, column=3, sticky="w", padx=8)
        tk.Label(self, text="If ZIP and folder both exist (same stem):").grid(row=r, column=4, sticky="e", padx=8)
        ttk.Combobox(self, textvariable=self.prefer_pkg, values=["folder","zip"]).grid(row=r, column=5, sticky="w", padx=8); r += 1

        tk.Checkbutton(self, text="Auto-set symbol Footprint when a single footprint is imported", variable=self.autoset).grid(row=r, column=0, columnspan=7, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Overwrite existing items with same name", variable=self.overwrite).grid(row=r, column=0, columnspan=7, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Process recursively when source is a directory", variable=self.recurse).grid(row=r, column=0, columnspan=7, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Ignore Footprint when de-duplicating (recommended)", variable=self.ignore_fp_dedupe).grid(row=r, column=0, columnspan=7, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Dry run (don’t write file; just show what would happen)", variable=self.dry_run).grid(row=r, column=0, columnspan=7, sticky="w", padx=10); r += 1

        btn_row = tk.Frame(self); btn_row.grid(row=r, column=0, columnspan=7, sticky="we", padx=8, pady=6)
        tk.Button(btn_row, text="Import", command=self.run).pack(side="left", padx=4)
        tk.Button(btn_row, text="Validate/Repair symbols file", command=self.repair_now).pack(side="left", padx=4)
        tk.Button(btn_row, text="View Library Parts", command=self.view_library_parts).pack(side="left", padx=4)
        tk.Button(btn_row, text="Audit Footprints (3D)", command=self.audit_footprints_gui).pack(side="left", padx=4)
        tk.Button(btn_row, text="Fix 3D Paths…", command=self.fix_3d_paths_gui).pack(side="left", padx=4)
        tk.Button(btn_row, text="Delete Parts…", command=self.delete_parts_gui).pack(side="left", padx=4)
        tk.Button(btn_row, text="Export Report", command=self.export_report).pack(side="left", padx=4)
        tk.Button(btn_row, text="Paths / Variables…", command=self.paths_tools_gui).pack(side="left", padx=4)

        tk.Button(btn_row, text="Quit", command=self.destroy).pack(side="right", padx=4); r += 1

        self.log = ScrolledText(self, wrap="word", height=28)
        self.log.grid(row=r, column=0, columnspan=7, sticky="nsew", padx=8, pady=8)
        self.grid_rowconfigure(r, weight=1); self.grid_columnconfigure(1, weight=1)

        self.results = []
        self.refresh_personal_candidates()

    # Prefs
    def load_prefs(self):
        try:
            if PREFS_FILE.exists():
                return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except Exception: pass
        return {}

    def save_prefs(self):
        try:
            data = {
                "last_source_dir": self.source.get(),
                "last_personal_dir": self.personal.get(),
                "sym_choice": self.sym_choice.get(),
                "fp_choice": self.fp_choice.get(),
                "models_root": self.models_root.get(),
                "force_relative_models": self.force_relative_models.get()
            }
            PREFS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception: pass

    def _initialdir_for(self, path_str: str):
        try:
            if path_str:
                p = Path(path_str).expanduser()
                if p.exists():
                    if p.is_file(): return str(p.parent)
                    if p.is_dir(): return str(p)
            if self.prefs.get("last_source_dir") and Path(self.prefs["last_source_dir"]).exists():
                return self.prefs["last_source_dir"]
        except Exception: pass
        return str(Path.home())

    def pick_source(self):
        initdir = self._initialdir_for(self.source.get())
        path = filedialog.askopenfilename(title="Choose vendor ZIP (or Cancel to pick a folder)", initialdir=initdir)
        if not path:
            path = filedialog.askdirectory(title="Choose a folder (single package or a directory of packages)", initialdir=initdir)
        if path:
            self.source.set(path)
            dir_path = str(Path(path).parent if Path(path).is_file() else Path(path))
            self.prefs["last_source_dir"] = dir_path
            self.save_prefs()

    def pick_personal(self):
        initdir = self._initialdir_for(self.personal.get())
        path = filedialog.askdirectory(title="Choose your personal library folder", initialdir=initdir)
        if path:
            self.personal.set(path)
            default3d = Path(path)/"3d"
            self.models_root.set(str(default3d))
            self.refresh_personal_candidates()
            self.save_prefs()

    def pick_models_root(self):
        init = self.models_root.get() or (Path(self.personal.get())/"3d")
        path = filedialog.askdirectory(title="Choose 3D models root (contains step/ and/or wrl/)", initialdir=str(init))
        if path:
            self.models_root.set(path)
            self.save_prefs()

    def refresh_personal_candidates(self):
        root = Path(self.personal.get()).expanduser()
        sym_candidates = []; fp_candidates = []
        if root.exists() and root.is_dir():
            sym_candidates = sorted([p.name for p in root.glob("*.kicad_sym")])
            fp_candidates = sorted([p.name for p in root.iterdir() if p.is_dir() and p.name.endswith(".pretty")])
        if "symbols.kicad_sym" not in sym_candidates:
            sym_candidates = ["symbols.kicad_sym"] + sym_candidates
        if "footprints.pretty" not in fp_candidates:
            fp_candidates = ["footprints.pretty"] + fp_candidates
        self.sym_combo["values"] = sym_candidates
        self.fp_combo["values"] = fp_candidates
        if self.sym_choice.get() not in sym_candidates: self.sym_choice.set(sym_candidates[0])
        if self.fp_choice.get() not in fp_candidates: self.fp_choice.set(fp_candidates[0])

    def current_sym_path(self) -> Path:
        return Path(self.personal.get()).expanduser() / self.sym_choice.get()
    def current_fp_dir(self) -> Path:
        return Path(self.personal.get()).expanduser() / self.fp_choice.get()
    def current_models_root(self) -> Path:
        root = self.models_root.get()
        return Path(root).expanduser() if root else (Path(self.personal.get()).expanduser() / "3d")

    # actions
    def logprint(self, *args):
        msg = " ".join(str(a) for a in args)
        self.log.insert("end", msg + "\n"); self.log.see("end"); self.update_idletasks()

    def _count_top_level(self, sym_path: Path):
        try: raw = sym_path.read_text(encoding="utf-8", errors="ignore")
        except Exception: return 0
        return len(parse_symbol_blocks(raw))

    def run(self):
        self.results = []
        try:
            src = Path(self.source.get()).expanduser()
            personal = Path(self.personal.get()).expanduser()
            sym_name = self.sym_choice.get().strip()
            fp_folder = self.fp_choice.get().strip()
            if not src.exists(): messagebox.showerror("Error", "Select a valid source (ZIP or folder)"); return
            if not personal.exists(): messagebox.showerror("Error", "Select your personal library folder"); return
            if not sym_name.endswith(".kicad_sym"): messagebox.showerror("Error", "Symbol file must end with .kicad_sym"); return
            if not fp_folder.endswith(".pretty"): messagebox.showerror("Error", "Footprint folder must end with .pretty"); return

            personal_sym = ensure_paths(personal, sym_name, fp_folder); self.save_prefs()

            pkgs = gather_packages(src, recurse=self.recurse.get())
            if not pkgs: messagebox.showwarning("Nothing found", "No KiCad parts found in that source."); return
            pkgs = prune_packages(pkgs, prefer=self.prefer_pkg.get())
            self.logprint(f"Discovered {len(pkgs)} package(s) after pruning parents and zip/folder duplicates.")

            initial_count = self._count_top_level(personal_sym)
            self.logprint(f"Starting import… (initial top-level symbols: {initial_count})\n")

            for idx, (kind, path) in enumerate(pkgs, 1):
                self.logprint(f"== [{idx}/{len(pkgs)}] {kind.upper()} :: {path}")
                def local_log(*a): self.logprint('   ', *a)
                res = process_one_package(
                    kind, path, personal, fp_folder, personal_sym,
                    fp_nick=self.nick.get(),
                    autoset_fp=self.autoset.get(),
                    overwrite=self.overwrite.get(),
                    mode=self.dedupe_mode.get(),
                    ignore_footprint_for_dedupe=self.ignore_fp_dedupe.get(),
                    dry_run=self.dry_run.get(),
                    log=local_log,
                    import_pair_models=True,
                    prefer_model_ext="step",
                    force_relative_models=self.force_relative_models.get()
                )
                self.results.append(res)
                for name, action, other in res.get("symbol_names_debug", []):
                    if action == "ADD":         self.logprint("   [SYM] ADD    ", name)
                    elif action == "SKIP_DUP":  self.logprint("   [SYM] SKIP   ", name, "==", other)
                    elif action == "RENAME":    self.logprint("   [SYM] RENAME ", name, "->", other)
                    elif action == "OVERWRITE": self.logprint("   [SYM] OVERWR ", name)
                if res["errors"]:
                    self.logprint("   ", "ERRORS:", "; ".join(res["errors"]))
                self.logprint("   ",
                    f"FP +{res['footprints_imported']} / skipped {res['footprints_skipped']} ;",
                    f"SYM +{res['symbols_added']} / renamed {res['symbols_renamed']} / overwritten {res['symbols_overwritten']} / content-dupes {res['symbols_skipped_content_dupe']} ;",
                    f"3D +{res['models_copied']} ; paired {res['models_paired']}"
                )

            if not self.dry_run.get():
                final_count = self._count_top_level(personal_sym)
                self.logprint(f"\nSummary: {personal_sym.name} contains {final_count} top-level symbol(s).")
                messagebox.showinfo("Import complete", f"Processed {len(pkgs)} package(s). {personal_sym.name} now has {final_count} symbol(s).")
            else:
                adds = sum(1 for r in self.results for (_n,a,_o) in r.get("symbol_names_debug", []) if a in ("ADD","RENAME"))
                est = initial_count + adds
                self.logprint(f"\nSummary (dry-run): initial {initial_count} -> estimated {est} top-level symbol(s) after this run.")
                messagebox.showinfo("Dry run complete", f"Processed {len(pkgs)} package(s). Estimated symbols after run: {est}.")
            self.logprint("Done. Reload libraries in KiCad to see updates.")
        except Exception as e:
            self.logprint("ERROR:", e); messagebox.showerror("Import failed", str(e))

    

    def paths_tools_gui(self):
        win = tk.Toplevel(self); win.title("KiCad Paths / Variables"); win.geometry("720x420")
        root_var = tk.StringVar(value=self.personal.get())
        tk.Label(win, text="Personal Library Root (for ${PERSONAL_LIB_DIR})").pack(anchor="w", padx=10, pady=(10,4))
        row = tk.Frame(win); row.pack(fill="x", padx=10)
        tk.Entry(row, textvariable=root_var).pack(side="left", fill="x", expand=True)
        def browse_root():
            p = filedialog.askdirectory(title="Choose personal library root", initialdir=self._initialdir_for(root_var.get()))
            if p: root_var.set(p)
        tk.Button(row, text="Browse…", command=browse_root).pack(side="left", padx=6)

        out = ScrolledText(win, wrap="word", height=12); out.pack(fill="both", expand=True, padx=10, pady=10)
        def plog(*a):
            msg = " ".join(str(x) for x in a)
            out.insert("end", msg + "\n"); out.see("end"); out.update_idletasks()

        btns = tk.Frame(win); btns.pack(fill="x", padx=10, pady=(0,10))
        def do_set_var():
            try:
                root = Path(root_var.get()).expanduser().resolve()
                ups = _update_kicad_path_variable(root, log=plog)
                if ups:
                    messagebox.showinfo("Done", f"Set PERSONAL_LIB_DIR in {len(ups)} KiCad config(s). Restart KiCad.")
                else:
                    messagebox.showinfo("Info", "No KiCad configs updated (already set or not found).")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        def do_rewrite_table():
            try:
                root = Path(root_var.get()).expanduser().resolve()
                ch = _rewrite_fp_lib_table(root, log=plog)
                if ch:
                    messagebox.showinfo("Done", f"Rewrote {len(ch)} fp-lib-table file(s). Reopen KiCad.")
                else:
                    messagebox.showinfo("Info", "No changes made to fp-lib-table.")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        def do_rewrite_models():
            try:
                root = Path(root_var.get()).expanduser().resolve()
                pretty_dir = self.current_fp_dir().resolve()
                n = _rewrite_models_in_pretty(pretty_dir, root, log=plog)
                messagebox.showinfo("Done", f"Updated {n} footprint(s). Then in KiCad: Tools→Update Footprints from Library.")
            except Exception as e:
                messagebox.showerror("Error", str(e))

        tk.Button(btns, text="Set ${PERSONAL_LIB_DIR}", command=do_set_var).pack(side="left", padx=4)
        tk.Button(btns, text="Rewrite fp-lib-table → ${PERSONAL_LIB_DIR}", command=do_rewrite_table).pack(side="left", padx=4)
        tk.Button(btns, text="Rewrite .pretty 3D paths → ${PERSONAL_LIB_DIR}", command=do_rewrite_models).pack(side="left", padx=4)
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=4)

    def export_report(self):
        if not self.results: messagebox.showwarning("No data", "Run an import first."); return
        ts = time.strftime("%Y%m%d-%H%M%S")
        save_json = filedialog.asksaveasfilename(title="Save JSON report", defaultextension=".json",
                                                 initialfile=f"kicad_personal_import_report_{ts}.json",
                                                 initialdir=self._initialdir_for(self.source.get()))
        if save_json:
            with open(save_json, "w", encoding="utf-8") as f: json.dump(self.results, f, indent=2)
        save_csv = filedialog.asksaveasfilename(title="Save CSV summary", defaultextension=".csv",
                                                initialfile=f"kicad_personal_import_report_{ts}.csv",
                                                initialdir=self._initialdir_for(self.source.get()))
        if save_csv:
            cols = ["package","kind","footprints_imported","footprints_skipped","footprints_errors",
                    "symbols_added","symbols_renamed","symbols_overwritten","symbols_skipped_content_dupe","models_copied","errors","models_paired"]
            with open(save_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
                for r in self.results:
                    row = {k: r.get(k, "") for k in cols}
                    if isinstance(row.get("errors"), list): row["errors"] = "; ".join(row["errors"])
                    w.writerow(row)
        messagebox.showinfo("Saved", "Report saved.")

    def repair_now(self):
        personal = Path(self.personal.get()).expanduser()
        sym_path = self.current_sym_path()
        if not personal.exists() or not sym_path.exists():
            messagebox.showerror("Error", "Select a valid personal folder and symbol file."); return
        self.logprint(f"Repairing {sym_path} …")
        raw = sym_path.read_text(encoding="utf-8", errors="ignore")
        merged = {}
        for name, block in parse_symbol_blocks(raw):
            merged[name] = sanitize_block(name, block)
        if not merged:
            self.logprint("   No (symbol …) blocks found. Leaving file as-is.")
            messagebox.showwarning("Nothing to repair", "No symbols found inside the file.")
            return
        backup = write_symbols_file(sym_path, merged)
        self.logprint(f"   Wrote clean symbols file. Backup at: {backup}")
        messagebox.showinfo("Repaired", f"{sym_path.name} rebuilt successfully.")

    def view_library_parts(self):
        personal = Path(self.personal.get()).expanduser()
        sym_path = self.current_sym_path()
        fp_dir = self.current_fp_dir()
        models_root = self.current_models_root()
        if not sym_path.exists():
            messagebox.showerror("Error", f"No symbol file at: {sym_path}"); return
        raw = sym_path.read_text(encoding="utf-8", errors="ignore")
        blocs = parse_symbol_blocks(raw)
        if not blocs:
            messagebox.showwarning("Empty", "No symbols found in your library."); return

        exact, stems, canon, files = _available_models_index(models_root)

        def footprint_of(block: str):
            m = re.search(r'\(\s*property\s+"Footprint"\s+"([^"]+)"', block)
            return m.group(1) if m else None

        def footprint_file(mod_name: str):
            if not mod_name: return None
            return fp_dir / f"{mod_name}.kicad_mod"

        def parse_models_in_footprint(mod_name: str):
            if not mod_name: return []
            fpath = footprint_file(mod_name)
            if not fpath or not fpath.exists(): return []
            txt = fpath.read_text(encoding="utf-8", errors="ignore")
            return [m.group(1) or m.group(2) for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt)]

        def model_found(path: str):
            return _choose_local_model_smart((exact, stems, canon, files), path) is not None

        rows = []
        stats = {"ok_fp":0,"missing_fp":0,"none_fp":0,"ok_3d":0,"some_3d":0,"none_3d":0}
        for name, block in sorted(blocs, key=lambda t: t[0].lower()):
            fp_prop = footprint_of(block)
            mod_name = fp_prop.split(":")[-1].strip() if fp_prop else None

            if mod_name:
                fpath = footprint_file(mod_name)
                if fpath and fpath.exists():
                    fp_text, fp_icon = "OK","✅"; stats["ok_fp"] += 1
                else:
                    fp_text, fp_icon = "Missing","❌"; stats["missing_fp"] += 1
            else:
                fp_text, fp_icon = "No prop","—"; stats["none_fp"] += 1

            if mod_name and fp_text == "OK":
                models = parse_models_in_footprint(mod_name)
                if not models:
                    three_d_text, three_d_icon = "None","❌"; found=0; total=0; stats["none_3d"] += 1
                else:
                    total = len(models)
                    found = sum(1 for m in models if model_found(m))
                    if found == 0:
                        three_d_text, three_d_icon = "None","❌"; stats["none_3d"] += 1
                    elif found == total:
                        three_d_text, three_d_icon = "All","✅"; stats["ok_3d"] += 1
                    else:
                        three_d_text, three_d_icon = "Partial","⚠️"; stats["some_3d"] += 1
            else:
                three_d_text, three_d_icon = "None","—"; found=0; total=0; stats["none_3d"] += 1

            rows.append((name, mod_name or "", f"{fp_icon} {fp_text}", f"{three_d_icon} {three_d_text}", f"{found}/{total}"))

        win = tk.Toplevel(self); win.title("Library Parts Overview"); win.geometry("1060x680")
        summary = f"Symbols: {len(blocs)}   |   Footprint: OK {stats['ok_fp']}, Missing {stats['missing_fp']}, None {stats['none_fp']}   |   3D: All {stats['ok_3d']}, Partial {stats['some_3d']}, None {stats['none_3d']}"
        legend = "Legend: ✅ OK, ⚠️ Partial, ❌ Missing/None, — Not applicable"
        tk.Label(win, text=summary).pack(anchor="w", padx=8, pady=(8,2))
        tk.Label(win, text=legend, fg="#555").pack(anchor="w", padx=8, pady=(0,6))

        search_frame = tk.Frame(win); search_frame.pack(fill="x", padx=8, pady=4)
        tk.Label(search_frame, text="Filter:").pack(side="left")
        qvar = tk.StringVar(); tk.Entry(search_frame, textvariable=qvar, width=40).pack(side="left", padx=6)

        cols = ("Symbol","Footprint Module","Footprint","3D","Models Found")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=24)
        for col, w, anchor in (("Symbol",420,"w"),("Footprint Module",280,"w"),("Footprint",140,"center"),("3D",140,"center"),("Models Found",120,"center")):
            tree.heading(col, text=col); tree.column(col, width=w, anchor=anchor)
        def refill():
            q = qvar.get().strip().lower()
            for i in tree.get_children(): tree.delete(i)
            for r in rows:
                if q in r[0].lower() or q in r[1].lower():
                    tree.insert("", "end", values=r)
        qvar.trace_add("write", lambda *_: refill()); refill()
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = tk.Frame(win); btns.pack(fill="x", padx=8, pady=(0,8))
        def export_csv():
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"personal_library_overview_{ts}.csv")
            if not path: return
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(cols)
                for r in rows: w.writerow(r)
            messagebox.showinfo("Saved", f"Exported {len(rows)} rows.")
        tk.Button(btns, text="Export to CSV", command=export_csv).pack(side="left")
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right")

    def audit_footprints_gui(self):
        fp_dir = self.current_fp_dir()
        models_root = self.current_models_root()
        if not fp_dir.exists():
            messagebox.showerror("Error", f"No footprint folder at: {fp_dir}"); return
        rows = audit_footprints(fp_dir, models_root)
        win = tk.Toplevel(self); win.title("Footprints 3D Audit"); win.geometry("900x600")
        ok = sum(1 for _,s,_,_ in rows if s=="All"); part = sum(1 for _,s,_,_ in rows if s=="Partial"); none = sum(1 for _,s,_,_ in rows if s=="None")
        tk.Label(win, text=f"Footprints: {len(rows)}   |   3D: All {ok}, Partial {part}, None {none}").pack(anchor="w", padx=8, pady=6)
        cols = ("Footprint Module","3D","Models Found")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=24)
        for col,w in (("Footprint Module",520),("3D",160),("Models Found",160)):
            tree.heading(col, text=col); tree.column(col, width=w, anchor="center" if col!="Footprint Module" else "w")
        for mod, status, icon, cnt in rows:
            tree.insert("", "end", values=(mod, f"{icon} {status}", cnt))
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        def export_csv():
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"footprints_3d_audit_{ts}.csv")
            if not path: return
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(cols)
                for mod, status, icon, cnt in rows:
                    w.writerow((mod, status, cnt))
            messagebox.showinfo("Saved", f"Exported {len(rows)} rows.")
        btns = tk.Frame(win); btns.pack(fill="x", padx=8, pady=(0,8))
        tk.Button(btns, text="Export to CSV", command=export_csv).pack(side="left")
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right")

    def fix_3d_paths_gui(self):
        fp_dir = self.current_fp_dir()
        models_root = self.current_models_root()
        sym_path = self.current_sym_path()
        if not fp_dir.exists():
            messagebox.showerror("Error", f"No footprint folder at: {fp_dir}"); return

        win = tk.Toplevel(self); win.title("Fix 3D Paths in Footprints"); win.geometry("720x380")
        dry = tk.BooleanVar(value=True)
        add_missing = tk.BooleanVar(value=True)
        pref = tk.StringVar(value="step")
        rel = tk.BooleanVar(value=self.force_relative_models.get())
        tk.Checkbutton(win, text="Dry run (show what would change; don’t write files)", variable=dry).pack(anchor="w", padx=12, pady=6)
        tk.Checkbutton(win, text="Add (model …) to footprints that have none (by filename / symbol)", variable=add_missing).pack(anchor="w", padx=12, pady=4)
        row = tk.Frame(win); row.pack(anchor="w", padx=12, pady=4)
        tk.Label(row, text="Prefer model extension:").pack(side="left")
        ttk.Combobox(row, values=["step","wrl"], textvariable=pref, width=10).pack(side="left", padx=6)
        tk.Checkbutton(win, text="Write RELATIVE (model …) paths", variable=rel).pack(anchor="w", padx=12, pady=4)
        out = ScrolledText(win, wrap="word", height=10); out.pack(fill="both", expand=True, padx=8, pady=8)

        def run_fix():
            changed, total, mods = fix_footprint_models(fp_dir, models_root, sym_path=sym_path, dry_run=dry.get(), add_if_missing=add_missing.get(), prefer_ext=pref.get(), force_relative=rel.get())
            out.insert("end", f"{'Dry run' if dry.get() else 'Wrote'}: {changed} of {total} footprints would be/was updated.\n")
            for m in mods[:300]:
                out.insert("end", f" - {m.name}\n")
            out.see("end")
            messagebox.showinfo("Done", f"{'Dry run' if dry.get() else 'Updated'} {changed} of {total} footprints.")
        tk.Button(win, text="Run", command=run_fix).pack(side="left", padx=12, pady=6)
        tk.Button(win, text="Close", command=win.destroy).pack(side="right", padx=12, pady=6)

    # Delete parts
    def delete_parts_gui(self):
        personal = Path(self.personal.get()).expanduser()
        sym_path = self.current_sym_path()
        fp_dir = self.current_fp_dir()
        if not sym_path.exists():
            messagebox.showerror("Error", f"Symbol file not found: {sym_path}"); return

        win = tk.Toplevel(self); win.title("Delete Parts"); win.geometry("900x640")
        backup = tk.BooleanVar(value=True); dry_run = tk.BooleanVar(value=True)
        delete_fp = tk.BooleanVar(value=True); delete_3d = tk.BooleanVar(value=True); only_if_unref = tk.BooleanVar(value=True)

        r = 0
        tk.Label(win, text=f"Symbols in: {sym_path.name}").grid(row=r, column=0, columnspan=4, sticky="w", padx=8, pady=(8,4)); r += 1
        tk.Label(win, text="Filter:").grid(row=r, column=0, sticky="e", padx=8)
        q = tk.StringVar(); tk.Entry(win, textvariable=q, width=40).grid(row=r, column=1, sticky="w", padx=6)
        tk.Checkbutton(win, text="Backup symbol file", variable=backup).grid(row=r, column=2, sticky="w")
        tk.Checkbutton(win, text="Dry run", variable=dry_run).grid(row=r, column=3, sticky="w"); r += 1
        tk.Checkbutton(win, text="Also delete referenced footprint(s)", variable=delete_fp).grid(row=r, column=0, columnspan=2, sticky="w", padx=8)
        tk.Checkbutton(win, text="Also delete referenced 3D model file(s)", variable=delete_3d).grid(row=r, column=2, columnspan=2, sticky="w", padx=8); r += 1
        tk.Checkbutton(win, text="Only delete FP/3D if unreferenced by remaining symbols", variable=only_if_unref).grid(row=r, column=0, columnspan=4, sticky="w", padx=8); r += 1

        listbox = tk.Listbox(win, selectmode="extended"); listbox.grid(row=r, column=0, columnspan=4, sticky="nsew", padx=8, pady=6)
        win.grid_rowconfigure(r, weight=1); win.grid_columnconfigure(1, weight=1); r += 1
        log = ScrolledText(win, wrap="word", height=10); log.grid(row=r, column=0, columnspan=4, sticky="nsew", padx=8, pady=8)

        def logprint(*a):
            log.insert("end", " ".join(str(x) for x in a) + "\n"); log.see("end"); log.update_idletasks()

        all_syms = list_symbols(sym_path)
        def refill(*_):
            listbox.delete(0, "end"); query = (q.get() or "").lower()
            for name, block in sorted(all_syms, key=lambda t: t[0].lower()):
                if query in name.lower(): listbox.insert("end", name)
        q.trace_add("write", refill); refill()

        def do_delete():
            sel = [listbox.get(i) for i in listbox.curselection()]
            if not sel:
                messagebox.showwarning("Nothing selected", "Select one or more symbols to delete."); return
            if not messagebox.askyesno("Confirm", f"Delete {len(sel)} symbol(s) from {sym_path.name}?"): return
            keep = {}; del_blocks = {}
            for name, block in all_syms:
                if name in sel: del_blocks[name] = block
                else: keep[name] = block

            del_fps = set(); keep_fps = set()
            for name, block in all_syms:
                fp = get_symbol_footprint(block)
                if not fp: continue
                (del_fps if name in sel else keep_fps).add(fp)

            fps_to_delete = set()
            if delete_fp.get():
                fps_to_delete = {fp for fp in del_fps if (fp not in keep_fps) or not only_if_unref.get()} if only_if_unref.get() else set(del_fps)

            logprint(f"Will remove {len(sel)} symbol(s). Footprints considered for deletion: {len(fps_to_delete)}")

            if not dry_run.get():
                try:
                    rebuild_symbols(sym_path, keep, backup=backup.get())
                    logprint(f"Wrote updated symbols file with {len(keep)} remaining symbol(s).")
                except Exception as e:
                    messagebox.showerror("Write failed", str(e)); return
            else:
                logprint("[Dry run] Not writing symbols file.")

            removed_fp_files = []; model_paths_to_consider = []
            for fp in sorted(fps_to_delete):
                mod_path = self.current_fp_dir() / f"{fp}.kicad_mod"
                if mod_path.exists():
                    if delete_3d.get():
                        for mp in models_in_footprint(mod_path):
                            model_paths_to_consider.append(mp)
                    if not dry_run.get():
                        try:
                            mod_path.unlink(); removed_fp_files.append(str(mod_path)); logprint(f"Deleted footprint: {mod_path.name}")
                        except Exception as e:
                            logprint(f"Failed to delete footprint {mod_path.name}: {e}")
                    else:
                        logprint(f"[Dry run] Would delete footprint: {mod_path.name}")

            removed_models = []
            if delete_3d.get() and model_paths_to_consider:
                still_referenced = set()
                if only_if_unref.get():
                    for other_mod in self.current_fp_dir().glob("*.kicad_mod"):
                        txt = other_mod.read_text(encoding="utf-8", errors="ignore")
                        for m in re.finditer(r'\(model\s+(?:"([^"]+)"|([^\s)]+))', txt):
                            still_referenced.add(m.group(1) or m.group(2))
                personal_root = Path(self.personal.get()).expanduser()
                unique_models = sorted(set(model_paths_to_consider))
                for model_path in unique_models:
                    if only_if_unref.get() and model_path in still_referenced: continue
                    abs_model = expand_personal_var(model_path, personal_root)
                    if abs_model.exists():
                        if not dry_run.get():
                            try:
                                abs_model.unlink(); removed_models.append(str(abs_model)); logprint(f"Deleted 3D model: {abs_model.name}")
                            except Exception as e:
                                logprint(f"Failed to delete 3D model {abs_model.name}: {e}")
                        else:
                            logprint(f"[Dry run] Would delete 3D model: {abs_model}")
            msg = f"Deleted symbols: {len(sel)}"
            if delete_fp.get(): msg += f", footprints removed: {len(removed_fp_files) if not dry_run.get() else len(fps_to_delete)}"
            if delete_3d.get(): msg += f", 3D removed: {len(removed_models) if not dry_run.get() else 'varies'}"
            messagebox.showinfo("Done", msg); win.destroy()

        btns = tk.Frame(win); btns.grid(row=r+1, column=0, columnspan=4, sticky="we", padx=8, pady=(0,8))
        tk.Button(btns, text="Delete selected", command=do_delete).pack(side="left", padx=6)
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=6)

def main():
    app = App(); app.mainloop()

if __name__ == "__main__":
    main()
