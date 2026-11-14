"""Aggregate the pilot census (scripts/run_census.py output) into frontend/static/census.json.

Input: a JSON-lines file, one record per catalogued trail attempted (Borlaff et al. 2026 Zenodo list).
Output: static/census.json with the distributions the site draws, plus thumbnails for a few examples.
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/claude/census/results.jsonl")
IMG_SRC = ROOT / "backend" / "cache" / "img"
IMG_DST = ROOT / "frontend" / "static" / "img"

recs = [json.loads(l) for l in SRC.open() if l.strip()]
attempted = [r for r in recs if "error" not in r]
on_pair = [r for r in attempted if not r.get("skipped")]
found = [r for r in on_pair if r.get("n_streaks_flagged", 0) > 0]
measured = [(r, s) for r in on_pair for s in r.get("streaks", [])]
with_T = [(r, s) for r, s in measured if s.get("temp_k")]

def reflected_corrected_T(s):
    """Blackbody fit of the long points after removing the reflected-sunlight part implied by the short camera."""
    from scipy.optimize import curve_fit
    H = 14387.77
    bnu = lambda l, T: l ** -3 / np.expm1(H / (l * T))
    sun = lambda l: bnu(l, 5772.0)
    pts = [p for p in s["spectrum"] if p["camera"] == "long" and p["value"] > 0]
    if len(pts) < 4 or s["flux_short"] <= 0:
        return None
    lam = np.array([p["um"] for p in pts]); F = np.array([p["value"] for p in pts]); E = np.array([p["err"] for p in pts]) + 0.05 * F
    F = F - s["flux_short"] * sun(lam) / sun(s["wavelength_short_um"])
    if lam.min() < 3.7 or np.median(F / E) < 4:
        return None
    try:
        p, _ = curve_fit(lambda l, b, T: b * bnu(l, T) / bnu(4.7, T), lam, F, p0=(np.median(F), 300), sigma=E, bounds=([0, 120], [np.inf, 900]))
        return round(float(p[1]))
    except Exception:  # noqa: BLE001
        return None

def detection(s, cam):
    """(flux, 1-sigma error) for one camera from the per-segment points: median flux, median err / sqrt(n)."""
    pts = [p for p in s["spectrum"] if p["camera"] == cam]
    if not pts:
        return None, None
    return float(np.median([p["value"] for p in pts])), float(np.median([abs(p["err"]) for p in pts]) / np.sqrt(len(pts)))

rows = []
for r, s in measured:
    fl, el = detection(s, "long"); fs, es = detection(s, "short")
    if fl is None or el is None or not (fl > 3 * el):
        continue                                   # no long-camera detection: not a measurement
    if fs is not None and es and fs > 3 * es:
        ratio, limit = fl / fs, False
    else:
        ratio, limit = (fl / (3 * es) if es else None), True   # short camera undetected: lower limit
    T, Trng = s.get("temp_k"), s.get("temp_range_k")
    if T and Trng and (Trng[1] - Trng[0] > 100):
        T, Trng = None, None                       # keep only usefully constrained temperatures
    rows.append({
        "ratio_is_limit": limit, "ratio": ratio, "long_snr": round(fl / el, 1),
        "obsid": r["OBSID"], "date": s["date"][:10], "pair": s["detectors"],
        "ratio_stars": s.get("ratio_stars"), "n_stars": s.get("n_stars"),
        "ratio_reflected_only": s.get("ratio_reflected_only"),
        "temp_k": T, "temp_range_k": Trng, "temp_k_refl_corr": reflected_corrected_T(s) if T else None,
        "length_deg": round(s["length_deg"], 2), "um_short": round(s["wavelength_short_um"], 2), "um_long": round(s["wavelength_long_um"], 2),
        "half": s.get("time_half_hint"), "png_long": s.get("png_long"), "png_short": s.get("png_short"), "png_full": s.get("png_full"),
    })

ratios = [x["ratio"] for x in rows if x["ratio"] and not x["ratio_is_limit"]]
limits = [x["ratio"] for x in rows if x["ratio"] and x["ratio_is_limit"]]
temps = [x["temp_k"] for x in rows if x["temp_k"]]
stars = [x["ratio_stars"] for x in rows if x["ratio_stars"]]
out = {
    "source": "Borlaff et al. 2026 SPHEREx trail catalogue (Zenodo 10.5281/zenodo.17957748), DR1 reprocessing of the same exposures at IRSA",
    "n_catalogued": 2182, "n_attempted": len(attempted), "n_on_long_pair": len(on_pair), "n_flagged_found": len(found),
    "n_measured": len(rows), "n_with_temp": len(temps), "n_ratio": len(ratios), "n_ratio_limits": len(limits),
    "ratio_median": float(np.median(ratios)) if ratios else None,
    "ratio_p16_p84": [float(np.percentile(ratios, 16)), float(np.percentile(ratios, 84))] if len(ratios) > 3 else None,
    "stars_median": float(np.median(stars)) if stars else None,
    "temp_median": float(np.median(temps)) if temps else None,
    "temp_p16_p84": [float(np.percentile(temps, 16)), float(np.percentile(temps, 84))] if len(temps) > 3 else None,
    "rows": sorted(rows, key=lambda x: (x["ratio_is_limit"], -(x["ratio"] or 0))),
}
# thumbnails for the six brightest with a temperature
IMG_DST.mkdir(parents=True, exist_ok=True)
ex = [x for x in out["rows"] if x["temp_k"]][:6]
for x in ex:
    for k in ("png_long", "png_short", "png_full"):
        if x.get(k) and (IMG_SRC / Path(x[k]).name).exists():
            shutil.copy(IMG_SRC / Path(x[k]).name, IMG_DST / Path(x[k]).name)
out["examples"] = [x["obsid"] for x in ex]
(ROOT / "frontend" / "static" / "census.json").write_text(json.dumps(out))
print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
