"""Freeze the demo targets and the satellite showcase into frontend/static/.

The website loads these first, so the demo works instantly even if the backend is asleep
or NASA's archive is busy. Re-run after new SPHEREx releases:  python -m scripts.export_static
"""
import json
import re
import shutil
from pathlib import Path

from backend import app as A
from backend import frames

OUT = Path(__file__).resolve().parents[1] / "frontend" / "static"


def copy_pngs(obj):
    for name in set(re.findall(r'img/([\w.\-]+\.png)', json.dumps(obj))):
        shutil.copy2(frames.IMG / name, OUT / "img" / name)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "img").mkdir(parents=True)
    targets = A.targets()
    (OUT / "targets.json").write_text(json.dumps(targets))
    cached = sorted(frames.spherex.CACHE.glob("result_*.json"), key=lambda f: f.stat().st_mtime)
    for t in targets:
        try:
            d = A.get_frames(ra=None, dec=None, target=t["key"], size_deg=0.12, per_epoch=2)
        except Exception as e:                    # archive busy: fall back to the newest cached build
            print(t["key"], "live build failed, using cache:", e)
            d = next(frames.clean(json.loads(f.read_text())) for f in reversed(cached)
                     if json.loads(f.read_text())["target"]["name"] == t["name"])
        d["snapshot"] = True
        copy_pngs(d)
        (OUT / f"frames_{t['key']}.json").write_text(json.dumps(d, allow_nan=False))
        print(t["key"], len(d["frames"]), "frames")
    sc = A.showcase()
    for r in sc:
        for c in r["streaks"]:
            c.pop("catalog_checked", None)
    copy_pngs(sc)
    (OUT / "showcase.json").write_text(json.dumps(sc, allow_nan=False))
    print("showcase", sum(len(r["streaks"]) for r in sc), "streaks")
    print("total", sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) // 1024, "KB")


if __name__ == "__main__":
    main()
