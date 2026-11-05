"""
Real CAD models of bought parts from open libraries, fetched on demand and cached (never committed: some are 50 MB).

    from vendor_parts import find, vendor_step
    find("raspberry")                  # paths in the FreeCAD parts library that match
    pi = vendor_step("Electronics Parts/Boards/Raspberry/Raspberry Pi 4B/Raspberry Pi 4B.step")
    from vendor_parts import kicad
    ind = kicad("Inductor_SMD.3dshapes/L_Bourns_SRN6045TA.step")   # KiCad model: footprint frame, z = 0 on the PCB

Source: the FreeCAD parts library (github.com/FreeCAD/FreeCAD-library), every part CC-BY 3.0 (credit its author,
named in that repository's history). It holds boards (Raspberry Pi 4B / Pico, Arduino Uno / Nano / Mega, ESP32),
cameras, ultrasonic sensors, servos (SG90, ES08A, Futaba 3003), steppers, fans, heatsinks, connectors, fasteners and
more. The KiCad 3D models (gitlab.com/kicad/libraries/kicad-packages3D: connectors, chips, passives, switches, battery
holders, USB; CC-BY-SA 4.0 with the KiCad library exception for designs using them) are fetched file by file with
kicad(). Other good libraries (most need a login, or are not reachable from this sandbox): makers' own
STEP downloads (Raspberry Pi datasheets site, NVIDIA Jetson download centre, Arduino docs, ST, Pololu, SparkFun,
Adafruit's GitHub CAD repos), GrabCAD, McMaster-Carr, TraceParts, 3D ContentCentral, SnapMagic / Ultra Librarian.

Use a vendor model for looks and for checking a catalogue entry; boards.board() stays the part the checks read (its
ports and holes are data, and it is light enough for every collision check).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List

FREECAD_REPO = "https://github.com/FreeCAD/FreeCAD-library"
CACHE = Path(os.environ.get("PROMETHEUS_PARTS_CACHE", str(Path.home() / ".cache" / "prometheus" / "parts")))


def _repo() -> Path:
    """a shallow, blob-less clone: the file list without the 2 GB of models; files are fetched one at a time"""
    d = CACHE / "freecad-library"
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", FREECAD_REPO,
                        str(d)], check=True, env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}, timeout=600)
    return d


def find(pattern: str, ext=(".step", ".stp")) -> List[str]:
    out = subprocess.run(["git", "-C", str(_repo()), "ls-tree", "-r", "--name-only", "HEAD"], check=True,
                         capture_output=True, text=True, timeout=120).stdout.splitlines()
    p = pattern.lower()
    return [f for f in out if p in f.lower() and f.lower().endswith(ext)]


def fetch(path: str) -> Path:
    """the file at ``path`` in the FreeCAD library, cached. The library moves folders now and then (Oct 2026:
    "Electronics Parts/Boards/ESP32/..." became "Computing/SingleBoardComputers/ESP32/..."), so a path that is gone is
    found again by its file name; the download goes to a temporary file first, so a failure never leaves an empty file
    in the cache that later reads as a broken STEP."""
    dst = CACHE / "files" / path
    if dst.exists() and dst.stat().st_size == 0:
        dst.unlink()
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = path
        names = subprocess.run(["git", "-C", str(_repo()), "ls-tree", "-r", "--name-only", "HEAD"], check=True,
                               capture_output=True, text=True, timeout=120).stdout.splitlines()
        if path not in names:
            base = path.rsplit("/", 1)[-1].lower()
            hits = [n for n in names if n.rsplit("/", 1)[-1].lower() == base]
            if not hits:
                raise FileNotFoundError(f"FreeCAD library: no {path} (nor any file named {base})")
            src = hits[0]
        tmp = dst.with_name(dst.name + ".part")
        with open(tmp, "wb") as fh:
            subprocess.run(["git", "-C", str(_repo()), "cat-file", "blob", f"HEAD:{src}"], check=True, stdout=fh,
                           timeout=600)
        if tmp.stat().st_size == 0:
            tmp.unlink()
            raise IOError(f"FreeCAD library: {src} came back empty")
        tmp.replace(dst)
    return dst


def vendor_step(path: str):
    import build123d as bd
    return bd.import_step(str(fetch(path)))


# makers' own models (public downloads), fetched once into the cache
MAKER_MODELS = {
    "rpi5": ("https://datasheets.raspberrypi.com/rpi5/RaspberryPi5-step.zip", "rpi-5b_no_graphics.step", "MIT"),
}


def maker_step(key: str):
    """the maker's official STEP for a catalogue board (MAKER_MODELS), downloaded once and cached"""
    import io
    import urllib.request
    import zipfile
    import build123d as bd
    url, member, _lic = MAKER_MODELS[key]
    dst = CACHE / "makers" / key / member
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=300).read()
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                dst.write_bytes(z.read(member))
        else:
            dst.write_bytes(data)
    return bd.import_step(str(dst))


KICAD_RAW = "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/"
_SHAPES: dict = {}


def kicad(path: str):
    """A KiCad library 3D model (e.g. "Package_SO.3dshapes/SOIC-14_3.9x8.7mm_P1.27mm.step"), fetched once into the
    cache. KiCad models sit in their footprint's frame: origin at the footprint origin (pin 1 or the centre), z = 0 on
    the board's top face, mm; place one with Pos(x, y, board_top) * Rot(0, 0, angle). Returns a fresh copy."""
    import build123d as bd
    if path not in _SHAPES:
        dst = CACHE / "kicad" / Path(path).name
        if not dst.exists() or dst.stat().st_size < 4096:
            import urllib.request
            dst.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(KICAD_RAW + path, timeout=120) as r:
                data = r.read()
            if not data.startswith(b"ISO-10303-21"):
                raise FileNotFoundError(f"no KiCad 3D model at {path}")
            dst.write_bytes(data)
        shp = bd.import_step(str(dst))
        sol = shp.solids()
        _SHAPES[path] = sol[0] if len(sol) == 1 else bd.Compound(children=list(sol))
    return bd.Pos(0, 0, 0) * _SHAPES[path]
