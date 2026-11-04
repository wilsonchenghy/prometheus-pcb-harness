"""Parts as KiCad knows them: a part's symbol from KiCad's own symbol library (its pins' numbers, names and electrical
types, its datasheet, its default footprint), so a board can be wired by pin name and checked both ways - every
symbol pin has a pad and every pad a pin - and an ERC can run on the netlist before anything is placed or routed.

    import pcb
    b.place("U1", None, None, 0, "DRV8833", symbol="Driver_Motor:DRV8833PWP")   # (footprint: the symbol's own)
    b.net("AIN2", [("U1", "AIN2"), ("J4", "2")])                                 # (pin 15, by its name)

``zeus part Driver_Motor:DRV8833PWP`` prints the pin table, the footprint and the pin-to-pad check.
"""
from __future__ import annotations

import re
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


def _fetch(path: str) -> str:
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


def symbol(name: str) -> dict:
    """KiCad symbol "Lib:Name" -> {"name", "lib", "footprint", "datasheet", "description", "pins": [{"num", "name",
    "key", "type", "at": (x, y), "rot", "length", "unit"}], "body": [(x0, y0, x1, y1), ...]}; a derived symbol
    (``extends``) takes its parent's pins. Fetched once into the parts cache (KiCad 9's one file per symbol)"""
    if name in _SYM:
        return _SYM[name]
    import pcb
    lib, sym = name.split(":", 1)
    tree = pcb._sexpr(_fetch(f"{lib}.kicad_symdir/{sym}.kicad_sym"))
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
    pins, body = [], []
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
    out = {"name": name, "lib": lib, "footprint": props.get("Footprint", ""), "datasheet": props.get("Datasheet", ""),
           "description": props.get("Description", ""), "value": props.get("Value", sym), "pins": pins, "body": body}
    _SYM[name] = out
    return out


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
        if len(strong) > 1 and not same:                                                    # one name: VOUT, VOUT)
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
                lines += [f"  datasheet: its pin table not found (read {d['datasheet']} by hand)"]
            lines += [f"  datasheet agrees: {d['agree']} of {d['total']} pins"
                      + (f" (its pin table, page {d['page']})" if d["page"] else " (searched the first pages)")]
            for q in d["pins"]:
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
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Zeus datasheet check)"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        if not data.startswith(b"%PDF"):
            raise ValueError("the datasheet link did not give a PDF")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    import pypdf
    return [(p.extract_text() or "") for p in pypdf.PdfReader(str(dst)).pages]


# the same pin under the names datasheets and KiCad's symbols give it
_ALIASES = [{"VO", "VOUT", "OUT", "OUTPUT"}, {"VI", "VIN", "IN", "INPUT"}, {"GND", "GROUND", "GND/ADJ", "VSS"}]


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
    names = [{k} | next((g for g in _ALIASES if k.upper() in g), set()) for k in keys]
    named = [sum(1 for n in names if any(re.search(re.escape(k) + r"(?![A-Za-z0-9_])", t) for k in n)) for t in pages]
    hits = [sum(1 for n in names if _numbers(n, t, 0) or _numbers(n, t, re.I)) if named[i] >= 2 else 0
            for i, t in enumerate(pages)]
    tables = [i for i, t in enumerate(pages) if named[i] >= max(2, len(keys) // 2) and hits[i] >= 2
              and re.search(r"pin\s*(functions|descriptions?|configurations?|assignments|connections)", t, re.I)]
    tables.sort(key=lambda i: -hits[i])
    text = "\n".join(pages[i] for i in (tables or range(min(len(pages), 12))))
    exposed = bool(re.search(r"power\s*pad|ppad|thermal\s*pad|exposed\s*pad|\bEP\b|\bPAD\b", text, re.I))
    out, agree = [], 0
    for p in sorted(s["pins"], key=lambda p: (len(p["num"]), p["num"])):
        variants = {p["key"], re.sub(r"^n", "", p["key"]), p["key"].replace("n", "/", 1)} - {""}
        variants |= {a for g in _ALIASES if p["key"].upper() in g for a in g}
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
