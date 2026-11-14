"""SkyShift backend API.

Run locally:  uvicorn backend.app:app --reload   (from the repo root)
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from astropy.time import Time

from . import frames, spherex
from .targets import TARGETS, position_at

app = FastAPI(title="SkyShift API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.mount("/img", StaticFiles(directory=str(frames.IMG)), name="img")


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
        # centre on the object's position in 2026.0 (middle of the data); it moves a few
        # arcseconds across the epochs, which is exactly what the flipbook shows
        ra, dec = position_at(target, Time.now().decimalyear - 0.5)
        name, size_deg = t["name"], t["size_deg"]
    if ra is None or dec is None:
        raise HTTPException(400, "give ra and dec (degrees) or a target")
    try:
        return frames.build(ra, dec, size_deg=size_deg, per_epoch_det=per_epoch, name=name)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
