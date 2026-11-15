"""SkyShift backend API.

Run locally:  uvicorn backend.app:app --reload   (from the repo root)
"""
import json
import os
import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import frames, spherex, streaks
from .targets import TARGETS, position_at

MID_EPOCH = 2026.37          # decimal year, middle of DR1 + QR3

app = FastAPI(title="SkyShift API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.mount("/img", StaticFiles(directory=str(frames.IMG)), name="img")


# Long jobs (a new patch of sky: ~1 min; a satellite analysis: ~2 min) run in the background, so no
# proxy or host timeout can cut the request. The first call answers 202 {"pending": true}; the browser
# asks again every few seconds until the result is cached. Marker files make this work across the
# several worker processes a shared host may run.
JOBS = spherex.CACHE / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
STALE_S = 900


def deferred(key, path, fn):
    if path.exists():
        return frames.clean(json.loads(path.read_text()))
    run, err = JOBS / f"{key}.run", JOBS / f"{key}.err"
    if err.exists():
        msg = err.read_text()
        err.unlink(missing_ok=True)
        raise HTTPException(503, msg or "the NASA archive did not answer; try again")
    if run.exists() and time.time() - run.stat().st_mtime < STALE_S:
        return JSONResponse({"pending": True, "since_s": round(time.time() - run.stat().st_mtime)}, 202)
    run.write_text(str(os.getpid()))

    def work():
        try:
            fn()
        except Exception as e:                      # noqa: BLE001  report any failure to the next poll
            err.write_text(str(e)[:300])
        finally:
            run.unlink(missing_ok=True)

    threading.Thread(target=work, daemon=True).start()
    return JSONResponse({"pending": True, "since_s": 0}, 202)


@app.get("/api/health")
def health():
    return {"ok": True, "collections": spherex.COLLECTIONS}


@app.get("/api/targets")
def targets():
    return [{"key": k, "name": t["name"], "note": t["note"], "size_deg": t["size_deg"]}
            for k, t in TARGETS.items()]


@app.get("/api/frames")
def get_frames(
    ra: float | None = Query(None, description="Right ascension, degrees"),
    dec: float | None = Query(None, description="Declination, degrees"),
    target: str | None = Query(None, description="Test target key, e.g. barnard"),
    size_deg: float = Query(0.12, ge=0.03, le=0.3),
    per_epoch: int = Query(2, ge=1, le=6, description="Frames per epoch and detector"),
):
    name = None
    if target:
        if target not in TARGETS:
            raise HTTPException(404, f"unknown target '{target}'")
        t = TARGETS[target]
        # centre on the object's position in the middle of the data (fixed, so results cache);
        # it moves a few arcseconds across the epochs, which is exactly what the flipbook shows
        ra, dec = position_at(target, MID_EPOCH)
        name, size_deg = t["name"], t["size_deg"]
    if ra is None or dec is None:
        raise HTTPException(400, "give ra and dec (degrees) or a target")
    key, path = frames.result_path(ra, dec, size_deg, per_epoch)
    return deferred(key, path, lambda: frames.build(ra, dec, size_deg=size_deg, per_epoch_det=per_epoch, name=name))


IRSA_PREFIX = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"

# Exposures where we found and validated satellite streaks (near Barnard's Star, Aug 2026)
SHOWCASE = [
    {"url": IRSA_PREFIX + "qr3/level2/2026W34_1A/l2b-v27-2026-243/3/level2_2026W34_1A_0238_2D3_spx_l2b-v27-2026-243.fits", "det": 3},
    {"url": IRSA_PREFIX + "qr3/level2/2026W34_1A/l2b-v27-2026-243/6/level2_2026W34_1A_0431_2D6_spx_l2b-v27-2026-243.fits", "det": 6},
]


@app.get("/api/streaks")
def get_streaks(
    url: str = Query(..., description="IRSA URL of one SPHEREx exposure (from /api/frames)"),
    det: int = Query(..., ge=1, le=6),
):
    """Find every satellite streak in this exposure and measure it in both cameras (30-90 s first time)."""
    if not url.startswith(IRSA_PREFIX) or not url.endswith(".fits"):
        raise HTTPException(400, "url must be a SPHEREx image URL from IRSA")
    key, path = streaks.result_path(url, det)
    return deferred(key, path, lambda: streaks.analyse(url, det))


@app.get("/api/gaia")
def gaia(ra: float = Query(...), dec: float = Query(...), radius_deg: float = Query(0.1, ge=0.01, le=0.5), limit: int = Query(1500, ge=1, le=5000)):
    """Gaia DR3 sources around a position, for the sky-map overlay. Served from NASA/IPAC IRSA's copy of
    Gaia DR3 (Gator cone search); the ESA archive is the fallback. The browser cannot call either
    directly (no CORS), so the backend relays the query."""
    import json
    import urllib.parse
    import urllib.request
    cols = ["source_id", "ra", "dec", "phot_g_mean_mag", "pmra", "pmdec", "parallax"]
    try:
        from astropy.io import ascii as aascii
        q = urllib.parse.urlencode({"catalog": "gaia_dr3_source", "spatial": "cone", "objstr": f"{ra} {dec}",
                                    "radius": round(radius_deg * 3600), "outfmt": 1, "selcols": ",".join(cols)})
        with urllib.request.urlopen(urllib.request.Request("https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query?" + q,
                                                           headers=spherex.UA), timeout=60) as r:
            t = aascii.read(r.read().decode(), format="ipac")
        t.sort("phot_g_mean_mag")
        import numpy as np
        rows = [[None if np.ma.is_masked(row[c]) else (str(row[c]) if c == "source_id" else float(row[c])) for c in cols]
                for row in t[:limit]]
        return {"source": "Gaia DR3, via NASA/IPAC IRSA", "columns": cols, "rows": rows}
    except Exception:                                # noqa: BLE001  fall back to ESA
        pass
    adql = (f"SELECT TOP {limit} {', '.join(cols)} FROM gaiadr3.gaia_source "
            f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {radius_deg})) ORDER BY phot_g_mean_mag")
    url = "https://gea.esac.esa.int/tap-server/tap/sync?" + urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=spherex.UA), timeout=30) as r:
            d = json.loads(r.read())
    except Exception as e:                           # noqa: BLE001
        raise HTTPException(502, f"Gaia not reachable from the server: {e}")
    return {"source": "Gaia DR3, ESA Gaia Archive", "columns": [c["name"] for c in d["metadata"]], "rows": d["data"]}


@app.get("/api/showcase")
def showcase():
    """Pre-validated satellite streaks for the demo (the website normally uses its pre-built copy)."""
    out = []
    for s in SHOWCASE:
        r = get_streaks(s["url"], s["det"])
        if isinstance(r, JSONResponse):
            return r                                  # still computing
        out.append(r)
    return out
