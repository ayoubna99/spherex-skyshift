"""Name the satellite behind a streak.

Uses a Space-Track 3-line element file (NOT included in the repo: Space-Track data may not be
redistributed). Point SKYSHIFT_TLE at it, or drop it in backend/data/orbits*.txt.

Method: SPHEREx's own position during the exposure comes from the image header (X_SC_START/MID/END,
geocentric, km). Every catalogued object is propagated with SGP4 across the exposure; we look at
the direction from SPHEREx to the object and keep objects whose apparent track lies on the streak's
great circle and runs inside the streak's extent. Distance = |object - SPHEREx| at that moment.
"""
import glob
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from astropy.time import Time
from sgp4.api import Satrec, SatrecArray, jday

DATA = Path(__file__).parent / "data"

EPH_KEYS = ["X_SC_START", "Y_SC_START", "Z_SC_START", "X_SC", "Y_SC", "Z_SC",
            "X_SC_END", "Y_SC_END", "Z_SC_END", "MJD-BEG", "MJD-AVG", "MJD-END"]


def tle_path():
    p = os.environ.get("SKYSHIFT_TLE")
    if p and Path(p).exists():
        return p
    files = sorted(glob.glob(str(DATA / "orbits*.txt")))
    return files[-1] if files else None


_JAN1 = {}
CHUNK = 1500


@lru_cache(maxsize=2)
def load_catalog(path):
    """Parse a 3LE file. Returns {norad: [(epoch_mjd, name, l1, l2), ...]}."""
    cat = {}
    with open(path) as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    i = 0
    while i + 2 < len(lines):
        n, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (n.startswith("0 ") and l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        name = n[2:].strip()
        norad = l1[2:7].strip()                      # string: catalog uses Alpha-5 for ids > 99999
        yy, doy = int(l1[18:20]), float(l1[20:32])
        year = 2000 + yy if yy < 57 else 1900 + yy
        mjd = _JAN1.setdefault(year, Time(f"{year}-01-01", scale="utc").mjd) + doy - 1
        cat.setdefault(norad, []).append((mjd, name, l1, l2))
        i += 3
    return cat


def _closest(cat, mjd, max_age_days=4):
    names, sats, ids = [], [], []
    for norad, rows in cat.items():
        best = min(rows, key=lambda r: abs(r[0] - mjd))
        if abs(best[0] - mjd) > max_age_days:
            continue
        try:
            sats.append(Satrec.twoline2rv(best[2], best[3]))
        except Exception:
            continue
        names.append(best[1])
        ids.append(norad)
    return names, ids, sats


def _teme_to_gcrs(mjd):
    """Rotation matrix TEME -> GCRS at one instant (constant over a 2-minute exposure)."""
    from skyfield.api import load
    from skyfield.sgp4lib import TEME
    ts = load.timescale(builtin=True)
    t = ts.from_astropy(Time(mjd, format="mjd", scale="utc"))
    return TEME.rotation_at(t).T


def _unit(ra, dec):
    ra, dec = np.radians(ra), np.radians(dec)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def identify(eph, line_sky, tol_deg=0.1, max_angle_deg=1.0, n_t=921, top=3):
    """Return candidate objects for one streak, best first. eph: dict of EPH_KEYS from the header."""
    path = tle_path()
    if path is None or any(eph.get(k) is None for k in EPH_KEYS):
        return []
    cat = load_catalog(path)
    t0, t1 = eph["MJD-BEG"], eph["MJD-END"]
    names, ids, sats = _closest(cat, eph["MJD-AVG"])
    if not sats:
        return []

    # SPHEREx position across the exposure: quadratic through start / mid / end
    tm = np.array([t0, eph["MJD-AVG"], t1])
    P = np.array([[eph["X_SC_START"], eph["Y_SC_START"], eph["Z_SC_START"]],
                  [eph["X_SC"], eph["Y_SC"], eph["Z_SC"]],
                  [eph["X_SC_END"], eph["Y_SC_END"], eph["Z_SC_END"]]])
    ts = np.linspace(t0, t1, n_t)
    sc = np.stack([np.polyval(np.polyfit(tm - tm[1], P[:, k], 2), ts - tm[1]) for k in range(3)], axis=1)

    jd = ts + 2400000.5
    jd_i, fr = np.floor(jd), jd - np.floor(jd)
    R = _teme_to_gcrs(eph["MJD-AVG"])
    a, b = _unit(*line_sky[0]), _unit(*line_sky[1])
    n = np.cross(a, b)
    n /= np.linalg.norm(n)
    c = (a + b) / np.linalg.norm(a + b)
    ldir = np.cross(n, c)                                       # streak direction at its centre
    arc_len = np.degrees(np.arccos(np.clip(a @ b, -1, 1)))
    na, nb = np.cross(n, a), np.cross(b, n)

    out = []
    for c0 in range(0, len(sats), CHUNK):                       # chunks keep memory under ~300 MB
        err, r, _ = SatrecArray(sats[c0:c0 + CHUNK]).sgp4(jd_i, fr)   # (n, n_t, 3) TEME km
        r = r @ R.T
        rel = r - sc[None, :, :]
        dist = np.linalg.norm(rel, axis=2)
        u = rel / dist[..., None]
        off = np.degrees(np.arcsin(np.clip(u @ n, -1, 1)))      # offset from the streak's great circle
        inside = ((u @ na) > -0.002) & ((u @ nb) > -0.002) & ((u @ c) > 0) & (err == 0) & (dist > 50)
        cand = np.flatnonzero((inside.sum(1) >= 2) & (np.where(inside, np.abs(off), 99).min(1) <= tol_deg))
        for j in cand:
            i = c0 + j
            m = inside[j]
            k = np.flatnonzero(m)
            du = u[j][k[-1]] - u[j][k[0]]
            span = np.degrees(np.linalg.norm(du))
            if span < 0.5 * arc_len:                            # must run along most of the streak
                continue
            angle = np.degrees(np.arccos(min(1.0, abs(du @ ldir) / np.linalg.norm(du))))
            if angle > max_angle_deg:                           # must move the same way as the streak
                continue
            out.append({
                "name": names[i], "norad": ids[i],
                "offset_deg": round(float(np.median(np.abs(off[j][m]))), 3),
                "angle_deg": round(float(angle), 2),
                "distance_km": round(float(np.median(dist[j][m]))),
                "altitude_km": round(float(np.median(np.linalg.norm(r[j][m], axis=1))) - 6371),
                "crossing_s": round(float((ts[k[-1]] - ts[k[0]]) * 86400), 1),
                "time_utc": Time(float(np.median(ts[k])), format="mjd", scale="utc").isot[:19],
            })
        del err, r, rel, dist, u, off, inside
    out.sort(key=lambda c: c["offset_deg"] + 0.05 * c["angle_deg"])
    return out[:top]
