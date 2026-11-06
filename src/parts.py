"""Parts as KiCad knows them: a part's symbol from KiCad's own symbol library (its pins' numbers, names and electrical
types, its datasheet, its default footprint), so a board can be wired by pin name and checked both ways - every
symbol pin has a pad and every pad a pin - and an ERC can run on the netlist before anything is placed or routed.

    import pcb
    b.place("U1", None, None, 0, "DRV8833", symbol="Driver_Motor:DRV8833PWP")   # (footprint: the symbol's own)
    b.net("AIN2", [("U1", "AIN2"), ("J4", "2")])                                 # (pin 15, by its name)

``prometheus part Driver_Motor:DRV8833PWP`` prints the pin table, the footprint and the pin-to-pad check.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

KICAD_SYM_RAW = "https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/master/"
_SYM: Dict[str, dict] = {}

# pin types that drive a net, that need driving, that need power
DRIVERS = {"output", "power_out", "bidirectional", "tri_state", "passive", "open_collector", "open_emitter", "free"}
STRONG = {"output", "power_out"}                       # (two of these on one net fight)
NEEDS_DRIVE = {"input"}
POWER_IN = {"power_in"}


class SymbolNotFound(ValueError):
    pass


def pin_key(name: str) -> str:
    """a pin name as a board script writes it: KiCad's overbar markup ~{SLEEP} read as nSLEEP, case kept"""
    n = re.sub(r"~\{([^}]*)\}", r"n\1", name or "")
    return n.replace("~", "").strip()


SYM_DIRS: List[Path] = [Path(__file__).parent / "pcb_symbols"]   # (project symbol folders, searched first: a
                                                         #  board's own symbols/ (pcb_cli.load adds it), then the
                                                         #  harness's own, for chips KiCad has none of: symgen.py)


def _fetch(path: str) -> str:
    for d in SYM_DIRS:
        own = Path(d) / path
        if own.exists():
            return own.read_text()
    from vendor_parts import CACHE
    dst = CACHE / "kicad_sym" / path.replace("/", "__")
    if dst.exists() and dst.stat().st_size > 50:
        return dst.read_text()
    try:
        with urllib.request.urlopen(KICAD_SYM_RAW + urllib.parse.quote(path), timeout=60) as r:
            data = r.read().decode()
    except Exception as e:
        raise SymbolNotFound(f"symbol file {path} is not in KiCad's library ({e})") from None
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(data)
    return data


def _hidden(top, key: str) -> bool:
    """whether the symbol hides its pins' names / numbers: (pin_names (hide yes)), or KiCad 6's bare (pin_names hide)"""
    for x in top:
        if isinstance(x, list) and x[:1] == [key]:
            return "hide" in x[1:] or any(isinstance(y, list) and y[:2] == ["hide", "yes"] for y in x)
    return False


def _drawing(sub) -> List[dict]:
    """a symbol unit's graphics, as KiCad draws them: {"t": "poly", "pts", "fill"} (lines, triangles),
    {"t": "rect", "a", "b", "fill"}, {"t": "circle", "c", "r", "fill"}, {"t": "arc", "s", "m", "e"}; symbol mm, y up"""
    out = []
    xy = lambda n: (float(n[1]), float(n[2]))
    for g in [x for x in sub if isinstance(x, list) and x and x[0] in ("polyline", "rectangle", "circle", "arc")]:
        kid = {x[0]: x for x in g if isinstance(x, list) and x}
        fill = next((y[1] for y in kid.get("fill", []) if isinstance(y, list) and y[:1] == ["type"]), "none")
        if g[0] == "polyline" and "pts" in kid:
            out.append({"t": "poly", "pts": [xy(q) for q in kid["pts"][1:] if isinstance(q, list)], "fill": fill})
        elif g[0] == "rectangle":
            out.append({"t": "rect", "a": xy(kid["start"]), "b": xy(kid["end"]), "fill": fill})
        elif g[0] == "circle":
            out.append({"t": "circle", "c": xy(kid["center"]), "r": float(kid["radius"][1]), "fill": fill})
        elif g[0] == "arc" and "mid" in kid:
            out.append({"t": "arc", "s": xy(kid["start"]), "m": xy(kid["mid"]), "e": xy(kid["end"])})
    return out


def symbol(name: str) -> dict:
    """KiCad symbol "Lib:Name" -> {"name", "lib", "footprint", "datasheet", "description", "pins": [{"num", "name",
    "key", "type", "at": (x, y), "rot", "length", "unit"}], "body": [(x0, y0, x1, y1), ...]}; a derived symbol
    (``extends``) takes its parent's pins. Fetched once into the parts cache (KiCad 9's one file per symbol)"""
    if name in _SYM:
        return _SYM[name]
    import pcb
    lib, sym = name.split(":", 1)
    tree = pcb._sx_parse(_fetch(f"{lib}.kicad_symdir/{sym}.kicad_sym"))
    top = next((s for s in pcb._find(tree, "symbol") if len(s) > 1 and s[1] == sym), None)
    if top is None:
        raise SymbolNotFound(f"symbol {name} is not in KiCad's library")
    props = {p[1]: p[2] for p in pcb._find(top, "property") if len(p) > 2}
    ext = next((x[1] for x in top if isinstance(x, list) and x and x[0] == "extends"), None)
    if ext:                                             # (an alias: the parent's pins and drawing, its own fields)
        base = symbol(f"{lib}:{ext}")
        out = dict(base, name=name, footprint=props.get("Footprint") or base["footprint"],
                   datasheet=props.get("Datasheet") or base["datasheet"],
                   description=props.get("Description") or base["description"])
        _SYM[name] = out
        return out
    pins, body, draw = [], [], []
    for sub in [x for x in top if isinstance(x, list) and x and x[0] == "symbol"]:
        unit = int(sub[1].rsplit("_", 2)[-2]) if re.search(r"_\d+_\d+$", sub[1]) else 0
        for p in [x for x in sub if isinstance(x, list) and x and x[0] == "pin"]:
            at = next(x for x in p if isinstance(x, list) and x[0] == "at")
            nm = next((x[1] for x in p if isinstance(x, list) and x[0] == "name"), "")
            num = next((x[1] for x in p if isinstance(x, list) and x[0] == "number"), "")
            ln = next((float(x[1]) for x in p if isinstance(x, list) and x[0] == "length"), 2.54)
            pins.append({"num": str(num), "name": nm, "key": pin_key(nm), "type": p[1], "unit": unit,
                         "at": (float(at[1]), float(at[2])), "rot": float(at[3]) if len(at) > 3 else 0.0, "length": ln})
        for r in [x for x in sub if isinstance(x, list) and x and x[0] == "rectangle"]:
            s = next(x for x in r if isinstance(x, list) and x[0] == "start")
            e = next(x for x in r if isinstance(x, list) and x[0] == "end")
            body.append((float(s[1]), float(s[2]), float(e[1]), float(e[2])))
        draw += _drawing(sub)
    out = {"name": name, "lib": lib, "footprint": props.get("Footprint", ""), "datasheet": props.get("Datasheet", ""),
           "description": props.get("Description", ""), "value": props.get("Value", sym), "pins": pins, "body": body,
           "draw": draw, "hide_names": _hidden(top, "pin_names"), "hide_numbers": _hidden(top, "pin_numbers")}
    _SYM[name] = out
    return out


def _symbol_names() -> List[str]:
    """every symbol in the installed KiCad's library, "Lib:Name" (read from its symbols folder once, then cached)"""
    import json
    import shutil
    from vendor_parts import CACHE
    cache = CACHE / "kicad_symbol_names.json"
    if cache.exists():
        return json.loads(cache.read_text())
    exe = Path(shutil.which("kicad-cli") or "").resolve()
    dirs = [Path(os.environ[k]) for k in ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR") if os.environ.get(k)] + \
           [exe.parent.parent / "SharedSupport" / "symbols", exe.parent.parent / "share" / "kicad" / "symbols",
            Path("/usr/share/kicad/symbols")]
    d = next((p for p in dirs if p.is_dir()), None)
    if d is None:
        raise FileNotFoundError("KiCad's symbol library not found: install KiCad (docs/setup.md) or set KICAD9_SYMBOL_DIR")
    names = []
    for f in sorted(d.glob("*.kicad_sym")):              # (top-level symbols only: their units are nested deeper)
        names += [f"{f.stem}:{n}" for n in re.findall(r'\n\t\(symbol "([^"]+)"', f.read_text(errors="ignore"))]
    for f in sorted(d.glob("*.kicad_symdir/*.kicad_sym")):   # (KiCad 10: a file per symbol)
        names.append(f"{f.parent.name[:-len('.kicad_symdir')]}:{f.stem}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(names))
    return names


@functools.lru_cache(maxsize=None)
def _symbol_patterns():
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", s.upper())
    return [(n, norm(n.split(":", 1)[1]), re.compile("^" + re.sub(r"X", "[A-Z0-9]", norm(n.split(":", 1)[1]))))
            for n in _symbol_names()]


def suggest_symbol(comp: dict) -> Optional[str]:
    """KiCad's symbol for a placed part, found by its value or part number (KiCad's lower-case x a wildcard) and
    kept only when its pins are the footprint's pads exactly; of several, the one named as the part is, else the
    longest that matches. None when KiCad has none that fits"""
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", s.upper())
    toks = [norm(t) for t in [comp.get("value", "")] + (comp.get("mpn") or "").split() if len(norm(t)) >= 4]
    if not toks:
        return None
    pads = {n for n in comp["pads"] if n}
    fits = []
    for name, sym, pat in _symbol_patterns():
        if not any(pat.match(t) or sym.startswith(t) for t in toks):
            continue
        try:
            if {p["num"] for p in symbol(name)["pins"]} == pads:
                fits.append(name)
        except SymbolNotFound:
            continue
    exact = [n for n in fits if norm(n.split(":", 1)[1]) in toks]
    return (exact or sorted(fits, key=lambda n: -len(n)) or [None])[0]


def resolve(comp: dict, pin: str) -> List[str]:
    """the pad numbers a net's pin names: a pad number as it is, else a symbol pin name (all of that name's numbers:
    a chip's several GND pins), else [] (the check reports it)"""
    pads = comp.get("pads", {})
    if pin in pads:
        return [pin]
    sym = comp.get("symbol")
    if not sym:
        return []
    hits = [p["num"] for p in sym["pins"] if p["key"] == pin or p["name"] == pin]
    if not hits:                                        # (case and overbar spelling forgiven: nsleep, SLEEP)
        low = pin.lower().lstrip("n~/")
        hits = [p["num"] for p in sym["pins"] if p["key"].lower().lstrip("n~/") == low]
    if not hits:                                        # (one of a pin's functions: PB4 for "XTAL2/PB4")
        hits = [p["num"] for p in sym["pins"] if low in {f.lower().lstrip("n~") for f in p["key"].split("/")}]
    return list(dict.fromkeys(hits))


def pin_check(board) -> List[str]:
    """every symbol pin has a pad and every pad a symbol pin, for each part placed with a symbol"""
    out = []
    for ref, c in board.comps.items():
        sym = c.get("symbol")
        if not sym:
            continue
        pads = {n for n in c["pads"] if n}
        nums = {p["num"] for p in sym["pins"]}
        for p in sym["pins"]:
            if p["num"] not in pads:
                out.append(f"{ref}: symbol pin {p['num']} ({p['key']}) has no pad on {c['fp'].split(':')[-1]}")
        for n in sorted(pads - nums, key=lambda s: (len(s), s)):
            out.append(f"{ref}: pad {n} of {c['fp'].split(':')[-1]} has no pin on symbol {sym['name']}")
    return out


def erc(board) -> List[dict]:
    """the electrical rules, from the symbols' pin types: a power input left unconnected or with nothing to feed it,
    an input left open or driven by nothing, two outputs tied, a no-connect pin wired. Returns critic notes
    ({"severity", "kind": "erc", "text", "refs"})"""
    notes = []
    pn = board.pad_net()
    types: Dict[Tuple[str, str], str] = {}
    names: Dict[Tuple[str, str], str] = {}
    for ref, c in board.comps.items():
        for p in (c.get("symbol") or {}).get("pins", []):
            types[(ref, p["num"])] = p["type"]
            names[(ref, p["num"])] = p["key"]

    def note(sev, text, refs):
        notes.append({"severity": sev, "kind": "erc", "text": text, "refs": sorted(set(refs))})
    unused: Dict[str, List[str]] = {}                   # (a chip's spare I/O: one note a chip, not one a pin)
    for (ref, num), t in sorted(types.items(), key=lambda kv: (kv[0][0], len(kv[0][1]), kv[0][1])):
        net = pn.get((ref, num))
        label = f"{ref}.{num} ({names[(ref, num)]}, {t.replace('_', ' ')})"
        if num in board.comps[ref].get("nc", ()):        # (left open on purpose: Board.nc)
            if net:
                note("warning", f"{label} is marked no-connect but is on net {net}", [ref])
            continue
        if t == "no_connect" and net:
            note("warning", f"{label} is a no-connect pin but is on net {net}", [ref])
        elif not net and t in POWER_IN:
            note("error", f"{label} is not connected", [ref])
        elif not net and t in NEEDS_DRIVE:
            note("error", f"{label} is left open: tie it high or low, or drive it", [ref])
        elif not net and t not in ("no_connect", "passive", "unspecified", "free"):
            unused.setdefault(ref, []).append(f"{num} {names[(ref, num)]}")
    for ref, ps in unused.items():
        note("info", f"{ref}: {len(ps)} pin{'s' if len(ps) > 1 else ''} not connected ("
             + ", ".join(ps[:8]) + (", ..." if len(ps) > 8 else "") + ")", [ref])
    for net, d in board.nets.items():
        pins = [tuple(p) for p in d["pins"]]
        ts = [types.get(p) for p in pins]
        known = [(p, t) for p, t in zip(pins, ts) if t]
        if not known:
            continue
        strong = [p for p, t in known if t in STRONG]
        same = len({r for r, _ in strong}) == 1 and len({names[p] for p in strong}) == 1   # (one chip's own pins of
        if len(strong) > 1 and not same and net not in getattr(board, "erc_allowed", {}):   # one name: VOUT, VOUT)
            note("error", f"net {net}: outputs {', '.join(f'{r}.{n}' for r, n in strong)} drive each other", [r for r, _ in strong])
        unknown = [p for p, t in zip(pins, ts) if not t]       # (a part with no symbol: a connector, a passive)
        if any(t in NEEDS_DRIVE for _, t in known) and not unknown and not any(t in DRIVERS for _, t in known):
            note("warning", f"net {net}: inputs only, nothing drives it", [r for r, _ in pins])
        pw = [p for p, t in known if t in POWER_IN]
        if pw and not unknown and not any(t in DRIVERS or t in STRONG for _, t in known) and len(pw) == len(pins):
            note("error", f"net {net}: power inputs only ({', '.join(f'{r}.{n}' for r, n in pw)}), nothing supplies it",
                 [r for r, _ in pw])
    return notes


def describe(name: str, board=None) -> str:
    """a symbol's pin table, its datasheet and footprint, and the pin-to-pad check against that footprint"""
    import pcb
    s = symbol(name)
    lines = [f"{s['name']}  ·  {s['description']}", f"  datasheet: {s['datasheet'] or '(none in the library)'}",
             f"  footprint: {s['footprint'] or '(none: give one)'}", "", "  pin  name            type"]
    for p in sorted(s["pins"], key=lambda p: (len(p["num"]), p["num"])):
        lines.append(f"  {p['num']:>3}  {p['key']:<15} {p['type'].replace('_', ' ')}")
    if s["footprint"]:
        try:
            pads = {n for n in pcb.footprint(s["footprint"])["pads"] if n}
            nums = {p["num"] for p in s["pins"]}
            miss, extra = sorted(nums - pads), sorted(pads - nums)
            lines += ["", f"  every pin has a pad: {'yes' if not miss else 'no: ' + ', '.join(miss)}",
                      f"  every pad has a pin: {'yes' if not extra else 'no: ' + ', '.join(extra)}"]
        except Exception as e:
            lines += ["", f"  footprint not checked: {e}"]
    try:                                                # (and the manufacturer's own pin table)
        d = datasheet_check(name)
        if d.get("error"):
            lines += [f"  datasheet: not checked ({d['error']})"]
        else:
            if d["page"] is None:
                lines += [f"  datasheet: its pin table not found, not checked (read {d['datasheet']} by hand)"]
            else:
                lines += [f"  datasheet agrees: {d['agree']} of {d['total']} pins (its pin table, page {d['page']})"]
            for q in d["pins"] if d["page"] is not None else []:
                if q["status"] != "agrees":
                    lines.append(f"    pin {q['num']} {q['name']}: {q['status']}"
                                 + (f" (the datasheet lists {', '.join(q['datasheet'])})" if q["datasheet"] else ""))
    except Exception as e:
        lines += [f"  datasheet: not read ({type(e).__name__}: {str(e)[:80]})"]
    return "\n".join(lines)


def datasheet_notes(board) -> List[dict]:
    """critic notes: each symbol part's pins against its datasheet (a pin the datasheet numbers otherwise: an error)"""
    notes, seen = [], {}
    for ref, c in board.comps.items():
        sym = c.get("symbol")
        if not sym:
            continue
        try:
            d = seen.get(sym["name"]) or datasheet_check(sym["name"])
            seen[sym["name"]] = d
        except Exception as e:
            notes.append({"severity": "info", "kind": "datasheet", "refs": [ref],
                          "text": f"{ref}: datasheet not read ({type(e).__name__})"})
            continue
        if d.get("error"):
            continue
        if d["page"] is None:                           # (no table found: nothing to hold the pins against)
            notes.append({"severity": "info", "kind": "datasheet", "refs": [ref],
                          "text": f"{ref}: the datasheet's pin table was not found; check its pins by hand"})
            continue
        bad = [q for q in d["pins"] if q["status"] == "differs"]
        for q in bad:
            notes.append({"severity": "error", "kind": "datasheet", "refs": [ref],
                          "text": f"{ref} pin {q['num']} ({q['name']}): the datasheet numbers it {', '.join(q['datasheet'])} "
                                  f"(page {d['page']})"})
        if not bad:
            notes.append({"severity": "info", "kind": "datasheet", "refs": [ref],
                          "text": f"{ref}: {d['agree']} of {d['total']} pins agree with the datasheet's pin table"})
    return notes


# ---- the datasheet: its pin table, read and held against the symbol ----

def datasheet_text(url: str) -> List[str]:
    """the datasheet's pages as text (fetched once into the parts cache; needs pypdf)"""
    import hashlib
    from vendor_parts import CACHE
    dst = CACHE / "datasheets" / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if not dst.exists() or dst.stat().st_size < 2000:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Prometheus datasheet check)"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        if not data.startswith(b"%PDF"):
            raise ValueError("the datasheet link did not give a PDF")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    import pypdf
    return [(p.extract_text() or "") for p in pypdf.PdfReader(str(dst)).pages]


# the same pin under the names datasheets and KiCad's symbols give it
_ALIASES = [{"VO", "VOUT", "OUT", "OUTPUT"}, {"VI", "VIN", "IN", "INPUT"}, {"GND", "GROUND", "GND/ADJ", "VSS"},
            {"RST", "RESET", "NRST", "NRESET"}]


def _names(key: str) -> set:
    """the names a datasheet may give a symbol pin: as it is, without its overbar n, each of its functions
    ("nRESET/PB5": RESET, PB5) and their other spellings"""
    out = {key, re.sub(r"^n", "", key), key.replace("n", "/", 1)}
    for part in key.split("/") if "/" in key else []:
        out |= {part, re.sub(r"^n", "", part)}
    for a in list(out):
        out |= next((g for g in _ALIASES if a.upper() in g), set())
    return {a for a in out if a}


def _numbers(variants, text, flags) -> List[str]:
    """the pin numbers the datasheet gives a pin under any of its names: "NAME 3", "NAME 3, 4" (a table row) or
    "3- NAME" (a numbered list)"""
    nums, listed = [], []
    for v in variants:
        e = re.escape(v) + r"(?![A-Za-z0-9_])"          # (no left edge: pypdf runs words together, "X7RVCP 9")
        for m in re.finditer(e + r"\s*((?:\d{1,3}(?:\s*[,/–-]\s*|\s+)){0,5}\d{1,3}|—)", text, flags):
            nums += re.findall(r"\d{1,3}", m.group(1))
        for m in re.finditer(r"(?:^[ \t]*|(?<=[a-z]\.))(\d{1,3})(?:[ \t]*[-–:.][ \t]*|[ \t]+)" + e, text, flags | re.M):
            listed.append(m.group(1))
    return listed or nums                               # (a numbered list says it plainly: "VIN" then the next
                                                        # line's "3- VOUT" is not VIN's number)


def datasheet_check(name: str, pages: Optional[Sequence[str]] = None) -> dict:
    """each of the symbol's pins looked up in its datasheet's pin table: the pin's name followed by its number(s)
    (one per package the table lists). Returns {"datasheet", "page", "pins": [{"num", "name", "status": "agrees" |
    "differs" | "not found", "datasheet": [numbers]}], "agree", "total"}"""
    s = symbol(name)
    if not s["datasheet"].lower().startswith("http"):
        return {"datasheet": s["datasheet"], "error": "the symbol has no datasheet link", "pins": []}
    pages = list(pages) if pages is not None else datasheet_text(s["datasheet"])
    # the table's pages: most of the pins named (a name a datasheet gives otherwise counts), a pin-table heading,
    # and pins given numbers there (a pinout drawing, its names and numbers apart, is not the table)
    keys = {q["key"] for q in s["pins"]}
    names = [_names(k) for k in keys]
    named = [sum(1 for n in names if any(re.search(re.escape(k) + r"(?![A-Za-z0-9_])", t) for k in n)) for t in pages]
    hits = [sum(1 for n in names if _numbers(n, t, 0) or _numbers(n, t, re.I)) if named[i] >= 2 else 0
            for i, t in enumerate(pages)]
    tables = [i for i, t in enumerate(pages) if named[i] >= max(2, len(keys) // 2) and hits[i] >= 2
              and re.search(r"pin\s*(functions|descriptions?|configurations?|assignments|connections)", t, re.I)]
    tables.sort(key=lambda i: -hits[i])
    if not tables:                                      # (no table: a guess from the first pages would mislead)
        return {"datasheet": s["datasheet"], "page": None, "agree": 0, "total": len(s["pins"]),
                "pins": [{"num": p["num"], "name": p["key"], "status": "not checked", "datasheet": []}
                         for p in s["pins"]]}
    text = "\n".join(pages[i] for i in tables)
    exposed = bool(re.search(r"power\s*pad|ppad|thermal\s*pad|exposed\s*pad|\bEP\b|\bPAD\b", text, re.I))
    out, agree = [], 0
    for p in sorted(s["pins"], key=lambda p: (len(p["num"]), p["num"])):
        variants = _names(p["key"])
        nums = _numbers(variants, text, 0) or _numbers(variants, text, re.I)   # (exact case first: "R" is not "r")
        nums = list(dict.fromkeys(nums))
        if p["num"] in nums:
            st = "agrees"
        elif p["type"] in ("passive", "power_in") and exposed and p["num"].isdigit() \
                and int(p["num"]) > max(int(q["num"]) for q in s["pins"] if q["num"].isdigit() and q is not p):
            st = "agrees"                               # (the exposed pad: the table names it, not by number)
        else:
            st = "differs" if nums else "not found"
        agree += st == "agrees"
        out.append({"num": p["num"], "name": p["key"], "status": st, "datasheet": nums})
    page = (tables[0] + 1) if tables else None
    return {"datasheet": s["datasheet"], "page": page, "pins": out, "agree": agree, "total": len(out)}


# ---- LCSC numbers: JLCPCB's own parts search (what its assembly order reads), for JLCPCB assembly ------------------
JLCSEARCH = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6}


def _si(v: str) -> Optional[float]:
    """'4u7' / '4.7u' / '53k6' / '330' / '4R7' -> the number (F or ohm)"""
    m = re.fullmatch(r"(\d+)([pnuµmRkKM])(\d+)|(\d+(?:\.\d+)?)\s*([pnuµmRkKM]?)", v.strip())
    if not m:
        return None
    if m.group(1):
        return float(f"{m.group(1)}.{m.group(3)}") * _SI[m.group(2)]
    return float(m.group(4)) * _SI[m.group(5)]


def _fmt(x: float, unit: str) -> str:
    for s, k in (("M", 1e6), ("k", 1e3), ("", 1.0), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if x >= k * 0.999 and (unit == "F" or k >= 1):
            return f"{x / k:.4g}{s}"
    return f"{x:g}"


JLC_DOWN = ""                                             # (why JLCPCB's search last failed)


def _jlc(q: str) -> List[dict]:
    import json
    import time
    from vendor_parts import CACHE
    f = CACHE / "lcsc" / (re.sub(r"[^\w.-]+", "_", q) + ".json")
    if f.exists():
        return json.loads(f.read_text())
    body = json.dumps({"keyword": q, "currentPage": 1, "pageSize": 10}).encode()
    for wait in (0, 2, 5):
        time.sleep(wait)
        try:
            req = urllib.request.Request(JLCSEARCH, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                rows = json.loads(r.read())["data"]["componentPageInfo"]["list"] or []
        except Exception as e:                           # (JLCPCB unreachable: said once by fill_lcsc)
            global JLC_DOWN
            JLC_DOWN = f"{type(e).__name__}: {str(e)[:120]}"
            continue
        out = [{"lcsc": int(c["componentCode"].lstrip("C")), "mfr": c.get("componentModelEn") or "",
                "package": c.get("componentSpecificationEn"), "description": c.get("describe") or "",
                "stock": c.get("stockCount") or 0, "is_basic": c.get("componentLibraryType") == "base"} for c in rows]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out))
        return out
    raise OSError(f"JLCPCB search unavailable ({q})")


def lcsc_match(comp: dict, found: List[dict], need: int = 1) -> Optional[dict]:
    """the catalogue entry that is this part: the MPN itself; or, for a resistor / capacitor / LED with no MPN found,
    the value (and a capacitor's voltage, at least the one asked) in the footprint's size. JLCPCB basic parts (no
    feeder fee) first, then the most stock; never one with less stock than ``need``"""
    fp = comp["fp"].split(":")[-1]
    size = (re.search(r"_(\d{4})_\d{4}Metric", fp) or [None, None])[1]
    mpn = (comp.get("mpn") or "").split()[-1].upper() if comp.get("mpn") else ""
    ok = [c for c in found if (c.get("stock") or 0) >= need]
    exact = [c for c in ok if mpn and c["mfr"].upper().replace("-", "") == mpn.replace("-", "")]
    if exact:
        return max(exact, key=lambda c: c["stock"])
    val, kind = (comp.get("value") or "").split(), fp.split("_")[0]
    if not val or not size:
        return None
    if kind in ("R", "C"):
        x = _si(val[0].rstrip("FΩ"))
        volts = next((float(v[:-1]) for v in val[1:] if re.fullmatch(r"\d+(\.\d+)?V", v)), 16.0 if kind == "C" else 0)
        if x is None:
            return None

        def same(c):
            d = c.get("description", "")
            if c.get("package") != size:
                return False
            if kind == "R":
                got = [_si(a + b) for a, b in re.findall(r"(\d+(?:\.\d+)?)([kKM]?)Ω", d)]
                return any(g and abs(g - x) < x * 1e-3 for g in got) and "±1%" in d
            got = [_si(a + b) for a, b in re.findall(r"(\d+(?:\.\d+)?)([pnuµm])F", d)]
            vs = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)V\b", d)]
            return any(g and abs(g - x) < x * 1e-3 for g in got) and vs and max(vs) >= volts and \
                any(t in d for t in ("X7R", "X5R", "C0G", "NP0"))
    elif kind == "LED":
        def same(c):
            return c.get("package") == size and val[0].lower() in c.get("description", "").lower()
    else:
        return None
    hits = [c for c in ok if same(c)]
    return max(hits, key=lambda c: (bool(c.get("is_basic")), c["stock"])) if hits else None


def lcsc_query(comp: dict) -> List[str]:
    """what to ask the catalogue for this part: its MPN, then its value in its size"""
    fp = comp["fp"].split(":")[-1]
    size = (re.search(r"_(\d{4})_\d{4}Metric", fp) or [None, None])[1]
    qs = [comp["mpn"].split()[-1]] if comp.get("mpn") else []
    val, kind = (comp.get("value") or "").split(), fp.split("_")[0]
    if val and size and kind in ("R", "C"):
        x = _si(val[0].rstrip("FΩ"))
        if x is not None:
            v = next((t for t in val[1:] if t.endswith("V")), "")
            qs.append(f"{_fmt(x, 'R')}Ω {size} 1%" if kind == "R" else f"{_fmt(x, 'F')}F {v} {size}".replace("  ", " "))
    elif val and size and kind == "LED":
        qs.append(f"{val[0]} {size} LED")
    return qs


def fill_lcsc(board, boards: int = 5) -> Dict[str, str]:
    """an LCSC number for every part without one (JLCPCB's catalogue, in stock for ``boards`` boards); the parts it
    fills are marked ``lcsc_auto``. {ref: C-number found}; a part with no match keeps none (the critic lists it)"""
    got: Dict[str, str] = {}
    for ref, c in board.comps.items():
        if c.get("lcsc"):
            continue
        for q in lcsc_query(c):
            try:
                hit = lcsc_match(c, _jlc(q), need=boards * 2)
            except OSError:                                  # (this query unanswered: the next one, or none)
                continue
            if hit:
                c["lcsc"], c["lcsc_auto"] = f"C{hit['lcsc']}", True
                got[ref] = c["lcsc"]
                break
    return got


def suggest_query(comp: dict) -> Optional[tuple]:
    """(query, words a hit must carry) from a part's footprint and value, for a part its MPN found nothing for: a
    pin header by its rows, pins and pitch; a crystal by its frequency and size. None for other parts"""
    # ponytail: headers and crystals only (the misses seen so far); add a family when a board shows one
    fp = comp["fp"].split(":")[-1]
    m = re.search(r"PinHeader_(\d)x(\d+)_P([\d.]+)mm", fp)
    if m:
        n = f"{m[1]}x{int(m[2])}P"
        return f"{n} {m[3]}mm pin header", [n.lower(), m[3] + "mm"]
    m = re.match(r"Crystal_SMD_(\d{4})", fp)
    val = (comp.get("value") or "").split()
    if m and val:
        f = val[0] if val[0].lower().endswith("hz") else val[0] + "Hz"
        return f"{f} {m[1]}", [f.lower(), m[1]]      # (no "crystal" in it: JLCPCB then ranks other load caps first)
    return None


def suggest_lcsc(board, boards: int = 5) -> Dict[str, dict]:
    """for each part still without an LCSC number, JLCPCB's likeliest part by its footprint and value (basic first,
    then stock), marked on the part as ``lcsc_suggested``: never put in the BOM or the cart, the engineer adopts one
    by giving ``lcsc=``, since a search this loose can find a near miss (a crystal of another load capacitance)"""
    got: Dict[str, dict] = {}
    for ref, c in board.comps.items():
        q = None if c.get("lcsc") else suggest_query(c)
        if not q:
            continue
        query, words = q
        try:
            found = _jlc(query)
        except OSError:
            continue
        vertical = "Vertical" in c["fp"]
        hits = [h for h in found if (h.get("stock") or 0) >= boards * 2
                and all(w in f"{h.get('package', '')} {h['description']}".lower() for w in words)
                and not (vertical and "right angle" in h["description"].lower())]
        if hits:
            h = max(hits, key=lambda h: (bool(h.get("is_basic")), h["stock"]))
            c["lcsc_suggested"] = got[ref] = {"lcsc": f"C{h['lcsc']}", "part": h["mfr"], "query": query,
                                              "stock": h["stock"], "basic": bool(h.get("is_basic")),
                                              "why": "no match for its MPN; found by value and package"}
    return got
