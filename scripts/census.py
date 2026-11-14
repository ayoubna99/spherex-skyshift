"""Pilot census: run the two-camera streak photometry on satellite trails catalogued by Borlaff et al. 2026.

Input: SPHEREx_trails_database.csv from Zenodo (DOI 10.5281/zenodo.17957748): trail midpoint ra/dec, position
angle, and the exposure ID. For each trail we ask IRSA which DR1 images contain the midpoint, pick the detector
pair of that exposure, run backend.streaks.analyse on it, and keep the measured streak that lies on the
catalogued trail. Output: a JSON list of measurements plus a sunlit/shadow geometry flag along the line of sight.

Usage: python -m scripts.census trails.csv out.json [--max N] [--workers 3] [--pairs 36,25,14]
"""
import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from astropy.coordinates import get_sun
from astropy.time import Time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import spherex, streaks  # noqa: E402

R_EARTH = 6371.0
PAIR_OF = {1: "14", 4: "14", 2: "25", 5: "25", 3: "36", 6: "36"}


def shadow_along_los(eph, mjd, ra, dec):
    """Range interval [d1, d2] (km) along the line of sight that lies inside Earth's umbra (cylindrical shadow),
    or None if the whole line of sight is sunlit. Uses SPHEREx's mid-exposure position from the header (km, ECI)."""
    try:
        P = np.array([eph["X_SC"], eph["Y_SC"], eph["Z_SC"]], float)
    except (KeyError, TypeError):
        return None
    if not np.all(np.isfinite(P)) or np.linalg.norm(P) < R_EARTH:
        return None
    t = Time(mjd, format="mjd")
    s = get_sun(t)
    sun = np.array([np.cos(s.dec.rad) * np.cos(s.ra.rad), np.cos(s.dec.rad) * np.sin(s.ra.rad), np.sin(s.dec.rad)])
    los = np.array([np.cos(np.radians(dec)) * np.cos(np.radians(ra)), np.cos(np.radians(dec)) * np.sin(np.radians(ra)), np.sin(np.radians(dec))])
    # point X(d) = P + d*los is in umbra if (X . sun) < 0 and |X - (X.sun) sun| < R_EARTH
    ds = np.arange(0, 5000, 5.0)
    X = P[None, :] + ds[:, None] * los[None, :]
    along = X @ sun
    perp = np.linalg.norm(X - along[:, None] * sun[None, :], axis=1)
    inside = (along < 0) & (perp < R_EARTH)
    if not inside.any():
        return None
    return [float(ds[inside].min()), float(ds[inside].max())]


def _line_from_catalog(exp, ra, dec, pa, half_deg=1.8):
    """Pixel line from the catalogue midpoint and position angle (PA = 0 north, + towards east)."""
    c = np.cos(np.radians(dec))
    dra, ddec = half_deg * np.sin(np.radians(pa)) / c, half_deg * np.cos(np.radians(pa))
    L = streaks.sky_to_line(exp, [ra - dra, ra + dra], [dec - ddec, dec + ddec])
    return streaks.clip_to_image(exp, L)


def _refine(exp, L, search=25):
    """Shift the line perpendicular to itself to the brightest ridge within +-search px (the catalogue midpoint is approximate)."""
    s = exp.resid
    t = np.linspace(L["t0"], L["t1"], 400)
    best, bestv = 0.0, -np.inf
    for off in np.arange(-search, search + 0.5, 1.0):
        pts = L["c"] + np.outer(t, L["d"]) + off * L["n"]
        x, y = np.clip(pts[:, 0].round().astype(int), 0, exp.nx - 1), np.clip(pts[:, 1].round().astype(int), 0, exp.ny - 1)
        v = np.nanmedian(s[y, x])
        if v > bestv:
            best, bestv = off, v
    L = dict(L)
    L["c"] = L["c"] + best * L["n"]
    return L, float(best)


def measure_catalog_trail(url, det, ra, dec, pa):
    """Two-camera wing photometry along the catalogued trail, whether or not the pipeline flagged it."""
    other = streaks.PAIR[det]
    s_det, l_det = min(det, other), max(det, other)
    urls = {det: url, other: streaks.partner_url(url, det, other)}
    short = streaks.Exposure(streaks.fetch_full(urls[s_det]))
    long_ = streaks.Exposure(streaks.fetch_full(urls[l_det]))
    L0 = _line_from_catalog(long_, ra, dec, pa)
    if L0 is None:
        return None
    L0, off = _refine(long_, L0)
    (r0, r1), (d0, d1) = streaks.line_to_sky(long_, L0)
    res = {}
    for tag, exp in (("short", short), ("long", long_)):
        L = streaks.clip_to_image(exp, streaks.sky_to_line(exp, [r0, r1], [d0, d1]))
        if L is None:
            return None
        near = np.abs((exp.X - L["c"][0]) * L["n"][0] + (exp.Y - L["c"][1]) * L["n"][1]) < 30
        fw = streaks.line_spread(exp, L["n"], near)
        segs = streaks.wing_segments(exp, L)
        if fw is None or not segs:
            return None
        for sg in segs:
            sg["flux"], sg["flux_err"] = sg["wing"] / fw, sg["err"] / fw
        res[tag] = {"L": L, "segs": segs}
    sr, n_stars = streaks.star_ratio(short, long_)
    fs = float(np.median([x["flux"] for x in res["short"]["segs"]])); fl = float(np.median([x["flux"] for x in res["long"]["segs"]]))
    es = float(np.median([x["flux_err"] for x in res["short"]["segs"]]) / np.sqrt(len(res["short"]["segs"])))
    el = float(np.median([x["flux_err"] for x in res["long"]["segs"]]) / np.sqrt(len(res["long"]["segs"])))
    lam_s = float(np.median([x["um"] for x in res["short"]["segs"]])); lam_l = float(np.median([x["um"] for x in res["long"]["segs"]]))
    temp = streaks.fit_temperature(res["long"]["segs"]) if l_det >= 5 else None
    flagged = bool(((long_.flags & streaks.STREAK) > 0).any())
    return {
        "date": short.date, "detectors": [s_det, l_det], "line_sky": [[float(r0), float(d0)], [float(r1), float(d1)]],
        "length_deg": float(np.degrees(np.arccos(np.clip(np.sin(np.radians(d0)) * np.sin(np.radians(d1)) + np.cos(np.radians(d0)) * np.cos(np.radians(d1)) * np.cos(np.radians(r0 - r1)), -1, 1)))),
        "flux_short": fs, "flux_long": fl, "flux_short_err": es, "flux_long_err": el,
        "wavelength_short_um": lam_s, "wavelength_long_um": lam_l,
        "ratio_long_short": float(fl / fs) if fs > 3 * es else None,
        "ratio_stars": sr, "n_stars": n_stars, "ratio_reflected_only": float(streaks.sun(lam_l) / streaks.sun(lam_s)),
        "temp_k": round(temp[0]) if temp else None, "temp_range_k": [round(temp[1]), round(temp[2])] if temp else None,
        "spectrum": [{"um": round(x["um"], 3), "value": round(x["flux"], 3), "err": round(x["flux_err"], 3), "camera": tag}
                     for tag in ("short", "long") for x in res[tag]["segs"]],
        "exposure_s": round((short.eph["MJD-END"] - short.eph["MJD-BEG"]) * 86400, 1) if short.eph.get("MJD-END") else None,
        "pipeline_flagged_streak_in_long": flagged, "refine_offset_px": off,
        "detected": bool(fl > 3 * el or fs > 3 * es), "eph": short.eph,
    }


def one(row, pairs):
    ra, dec, pa, obsid = float(row["ra"]), float(row["dec"]), float(row["pa"]), row["OBSID"]
    hits = [h for h in spherex.search(ra, dec, 0.001) if h["obs_id"] == obsid and h["collection"] == "spherex_dr1"]
    if not hits:
        return {"OBSID": obsid, "status": "no_dr1_image_at_midpoint"}
    det = min(h["detector"] for h in hits)
    if PAIR_OF[det] not in pairs:
        return {"OBSID": obsid, "status": f"pair_{PAIR_OF[det]}_skipped"}
    url = next(h["url"] for h in hits if h["detector"] == det)
    res = streaks.analyse(url, det)
    best, bestd = None, 1e9
    for s in res["streaks"]:
        (r0, d0), (r1, d1) = s["line_sky"]
        # perpendicular distance of the catalogue midpoint from the measured line (small-angle, deg)
        c = np.cos(np.radians(dec))
        ax, ay = (r1 - r0) * c, d1 - d0
        px, py = (ra - r0) * c, dec - d0
        n = np.hypot(ax, ay) or 1e-9
        dist = abs(ax * py - ay * px) / n
        if dist < bestd:
            best, bestd = s, dist
    if best is not None and bestd > 0.05:            # the flagged streak is a different trail in the same exposure
        best = None
    if best is None:
        m = measure_catalog_trail(url, det, ra, dec, pa)
        if m and m["detected"]:
            best, bestd = m, 0.0
            best["method"] = "catalogue_line"
            best["id"] = f"{obsid}_cat"
            eph = best.pop("eph")
            out = {"OBSID": obsid, "status": "ok", "detectors": [det, streaks.PAIR[det]], "n_streaks_flagged": res["n_streaks_flagged"],
                   "catalog_ra": ra, "catalog_dec": dec, "catalog_pa": pa, "mjd": float(row["MJDOBS"]), "url": url}
            out.update(best); out["midpoint_offset_deg"] = 0.0
            out["shadow_range_km"] = shadow_along_los(eph, eph.get("MJD-AVG") or float(row["MJDOBS"]), ra, dec)
            return out
        return {"OBSID": obsid, "status": "no_measurable_streak" if m else "line_outside_image", "detectors": [det, streaks.PAIR[det]],
                "catalog_ra": ra, "catalog_dec": dec, "flux_long": m and m["flux_long"], "flux_long_err": m and m["flux_long_err"]}
    out = {"OBSID": obsid, "status": "ok", "method": "pipeline_flag", "detectors": [det, streaks.PAIR[det]],
           "n_streaks_flagged": res["n_streaks_flagged"], "catalog_ra": ra, "catalog_dec": dec, "catalog_pa": pa,
           "mjd": float(row["MJDOBS"]), "url": url}
    if best:
        keep = ["id", "date", "length_deg", "flux_short", "flux_long", "wavelength_short_um", "wavelength_long_um",
                "ratio_long_short", "ratio_stars", "n_stars", "ratio_reflected_only", "temp_k", "temp_range_k",
                "spectrum", "time_half_hint", "exposure_s", "satellite", "catalog_checked", "line_sky",
                "png_short", "png_long", "png_full"]
        out.update({k: best.get(k) for k in keep})
        out["midpoint_offset_deg"] = float(bestd)
        try:
            exp = streaks.Exposure(streaks.fetch_full(url))
            out["shadow_range_km"] = shadow_along_los(exp.eph, exp.eph.get("MJD-AVG") or float(row["MJDOBS"]), ra, dec)
        except Exception as e:  # noqa: BLE001
            out["shadow_range_km"] = None
            out["shadow_error"] = str(e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv"); ap.add_argument("out")
    ap.add_argument("--max", type=int, default=60); ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--pairs", default="36,25"); ap.add_argument("--start", type=int, default=0)
    a = ap.parse_args()
    pairs = set(a.pairs.split(","))
    rows = list(csv.DictReader(open(a.csv)))[a.start:]
    out_path = Path(a.out)
    results = json.loads(out_path.read_text()) if out_path.exists() else []
    done = {(r["OBSID"], round(r.get("catalog_ra", -1), 3)) for r in results}
    todo = [r for r in rows if (r["OBSID"], round(float(r["ra"]), 3)) not in done][: a.max]
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(one, r, pairs): r for r in todo}
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001
                r = {"OBSID": futs[f]["OBSID"], "status": "error", "error": str(e)}
            results.append(r)
            out_path.write_text(json.dumps(results, indent=1))
            n_ok = sum(1 for x in results if x.get("status") == "ok")
            print(f"[{time.time()-t0:6.0f}s] {r['OBSID']} {r['status']}  ok so far: {n_ok}/{len(results)}", flush=True)


if __name__ == "__main__":
    main()
