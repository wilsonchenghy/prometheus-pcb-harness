"""The fabrication set for any routed board: copper plot, BOM, Gerbers + drill, the KiCad project, KiCad's own DRC
(when KiCad is installed), KiCad renders and the zips - as the other examples' ``export`` functions write it."""
import csv
import os
import tempfile

import pcb


def export(b, probs, out):
    chk = b.check()
    print(b.name, "route:", probs or "clean", "| check:", chk or "clean")
    os.makedirs(out, exist_ok=True)
    b.plot(os.path.join(out, "layers.png"))
    with open(os.path.join(out, "bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["refs", "qty", "value", "mpn", "footprint"])
        for r in b.bom():
            w.writerow([" ".join(r["refs"]), r["qty"], r["value"], r["mpn"], r["footprint"]])
    pcb.export_gerbers(b, os.path.join(out, "gerbers"))
    kpcb = pcb.export_kicad(b, os.path.join(out, "kicad"))[0]
    try:
        for refill in (False, True):
            d = pcb.kicad_drc(kpcb, refill=refill)
            print(f"KiCad DRC ({'KiCad refill' if refill else 'as exported'}):", len(d["errors"]), "errors,",
                  len(d["unconnected"]), "unconnected")
        mdir = os.path.join(tempfile.gettempdir(), "kicad_3d")
        pcb.kicad_models(b, mdir)
        pcb.render_kicad(kpcb, out, mdir)
    except Exception as e:                       # (no KiCad here: the Gerbers and the KiCad project are still written)
        print("KiCad DRC / renders skipped:", str(e)[:120])
    for d in ("gerbers", "kicad"):
        pcb.pack(os.path.join(out, d))
    return b
