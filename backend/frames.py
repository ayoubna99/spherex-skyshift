"""Turn SPHEREx cutouts at one sky position into flipbook frames.

Steps per request:
1. find all images (spherex.search) and group them into survey epochs (SPHEREx revisits every ~6 months);
2. pick a few frames per epoch and detector, download cutouts;
3. reproject every cutout onto ONE north-up grid centred on the target (SPHEREx images are rotated);
4. subtract the zodiacal-light model and the local background;
5. render PNGs with ONE brightness stretch per detector, so brightness changes are real;
6. stack each (epoch, detector) for a cleaner image and make epoch-to-epoch difference images.
"""
import hashlib
import json
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import ndimage
from astropy import log as astropy_log
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning
from PIL import Image
from reproject import reproject_interp

from . import spherex

astropy_log.setLevel("ERROR")
warnings.simplefilter("ignore", FITSFixedWarning)
warnings.filterwarnings("ignore", message=".*SIP.*")

IMG = spherex.CACHE / "img"
IMG.mkdir(parents=True, exist_ok=True)
PIX_DEG = 6.15 / 3600.0               # SPHEREx pixel size
BIT_STREAK = 1 << 25
BIT_TRANSIENT = 1 << 0
DET_BANDS = {1: "0.75–1.09 µm", 2: "1.10–1.62 µm", 3: "1.63–2.41 µm",
             4: "2.42–3.82 µm", 5: "3.83–4.41 µm", 6: "4.42–5.00 µm"}


def group_epochs(rows, gap_days=60):
    """Assign an epoch number to each row: a new epoch starts after a gap of > gap_days."""
    epoch, last = -1, None
    for r in rows:
        if last is None or r["mjd"] - last > gap_days:
            epoch += 1
        r["epoch"] = epoch
        last = r["mjd"]
    return epoch + 1


def pick(rows, per_epoch_det):
    """Pick up to N frames per (epoch, detector), spread evenly in time."""
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[(r["epoch"], r["detector"])].append(i)
    keep = []
    for idx in groups.values():
        if len(idx) <= per_epoch_det:
            keep += idx
        else:
            sel = np.linspace(0, len(idx) - 1, per_epoch_det).round().astype(int)
            keep += [idx[j] for j in sorted(set(sel))]
    return sorted(keep)


def target_wcs(ra, dec, size_deg):
    n = int(round(size_deg / PIX_DEG))
    n += n % 2 == 0                   # odd size: target sits on the centre pixel
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    w.wcs.cdelt = [-PIX_DEG, PIX_DEG]
    return w, n


def load_cutout(path, out_wcs, n, ra, dec):
    """Reproject one cutout onto the common grid. Returns (image, flags_summary, wavelength_um)."""
    with fits.open(path) as h:
        hdr = h["IMAGE"].header
        img = h["IMAGE"].data.astype(float)
        if "ZODI" in h:
            img = img - h["ZODI"].data
        flags = h["FLAGS"].data.astype(np.int64)
        w = WCS(hdr)
        # wavelength at the target position, from the spectral lookup table
        lam = None
        try:
            ww = WCS(hdr, fobj=h, key="W")
            x, y = w.world_to_pixel_values(ra, dec)
            lam = float(ww.pixel_to_world_values(x, y)[0])
        except Exception:             # noqa: BLE001
            pass
    rep, _ = reproject_interp((img, w), out_wcs, shape_out=(n, n), order="bilinear")
    rep -= np.nanmedian(rep)
    summary = {
        "streak": bool((flags & BIT_STREAK).any()),
        "streak_px": int(((flags & BIT_STREAK) > 0).sum()),
        "transient_px": int(((flags & BIT_TRANSIENT) > 0).sum()),
    }
    return rep, summary, lam


def _fill_holes(a, rounds=3):
    """Fill small NaN holes (saturated star cores) with the brightest neighbour, for display only."""
    from scipy import ndimage
    a = a.copy()
    for _ in range(rounds):
        nan = np.isnan(a)
        if not nan.any():
            break
        filled = ndimage.maximum_filter(np.where(nan, -np.inf, a), size=3)
        fix = nan & np.isfinite(filled)
        a[fix] = filled[fix]
    return a


def _outside(a, min_px=40):
    """Large NaN areas = outside the exposure (not small saturated holes)."""
    lab, n = ndimage.label(np.isnan(a))
    if not n:
        return np.zeros(a.shape, bool)
    sizes = ndimage.sum(np.ones_like(a), lab, index=np.arange(1, n + 1))
    big = np.flatnonzero(sizes >= min_px) + 1
    return np.isin(lab, big)


def _stretch(a, lo, hi, soft=0.05):
    out = _outside(a)
    a = np.nan_to_num(_fill_holes(a), nan=lo)
    a[out] = lo
    s = soft * (hi - lo) if hi > lo else 1.0
    v = np.arcsinh((a - lo) / s) / np.arcsinh((hi - lo) / s)
    return (np.clip(v, 0, 1) * 255).astype(np.uint8)


UPS = 6          # PNGs are upsampled smoothly (bicubic) so 6"-pixels don't look like blocks


def _save(arr, name):
    im = Image.fromarray(arr)
    im.resize((im.width * UPS, im.height * UPS), Image.BICUBIC).save(IMG / name, optimize=True)
    return f"img/{name}"


def save_gray(a, lo, hi, name):
    # FITS row 0 is the bottom; flip so north is up in the PNG
    return _save(_stretch(a, lo, hi)[::-1], name)


# false colour: long infrared -> red, middle -> green, short -> blue (first available of each)
COLOR_CHANNELS = ((6, 5, 4), (3, 2), (1, 2))


def save_color(stack_arr, ep, limits, name):
    chans = []
    for options in COLOR_CHANNELS:
        d = next((d for d in options if (ep, d) in stack_arr), None)
        if d is None:
            return None, None
        a = stack_arr[(ep, d)]
        f = np.nan_to_num(_fill_holes(a))
        f = f - ndimage.median_filter(f, 13)                          # remove patchy background
        sm = ndimage.gaussian_filter(f, 0.8)                          # tame pixel noise
        sm[_outside(a)] = np.nan
        chans.append((d, _stretch(sm, *limits[d], soft=0.015)))
    rgb = np.dstack([c for _, c in chans])[::-1]
    return _save(np.ascontiguousarray(rgb), name), [d for d, _ in chans]


def save_diff(d, lim, name):
    v = np.clip(np.nan_to_num(d) / lim, -1, 1)[::-1]
    rgb = np.zeros(v.shape + (3,), np.uint8)
    pos, neg = np.clip(v, 0, 1), np.clip(-v, 0, 1)
    rgb[..., 0] = (255 * pos).astype(np.uint8)                 # brighter now: orange
    rgb[..., 1] = (140 * pos + 120 * neg).astype(np.uint8)
    rgb[..., 2] = (255 * neg).astype(np.uint8)                 # brighter before: blue
    return _save(rgb, name)


def clean(x):
    """Replace NaN/inf with None so the result is valid JSON (some exposures lack a wavelength)."""
    if isinstance(x, float):
        return x if np.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x


def result_path(ra, dec, size_deg=0.12, per_epoch_det=2, dets=(1, 2, 3, 4, 5, 6)):
    key = hashlib.md5(f"v6|{ra:.5f}|{dec:.5f}|{size_deg:.4f}|{per_epoch_det}|{dets}|{spherex.COLLECTIONS}"
                      .encode()).hexdigest()[:12]
    return key, spherex.CACHE / f"result_{key}.json"


def build(ra, dec, size_deg=0.12, per_epoch_det=2, dets=(1, 2, 3, 4, 5, 6), name=None):
    key, cached = result_path(ra, dec, size_deg, per_epoch_det, dets)
    if cached.exists():
        return clean(json.loads(cached.read_text()))

    rows = [r for r in spherex.search(ra, dec) if r["detector"] in dets]
    n_epochs = group_epochs(rows)
    keep = pick(rows, per_epoch_det)
    sel = [rows[i] for i in keep]
    paths = spherex.fetch_many(sel, ra, dec, size_deg)
    out_wcs, n = target_wcs(ra, dec, size_deg)

    frames, arrays = [], {}
    for i, r in enumerate(sel):
        if i not in paths:
            continue
        try:
            a, summ, lam = load_cutout(paths[i], out_wcs, n, ra, dec)
        except Exception:             # noqa: BLE001 - skip unreadable cutout
            continue
        if np.isnan(a).mean() > 0.5:   # target near the image edge
            continue
        fid = r["obs_id"] + f"D{r['detector']}"
        arrays[fid] = a
        t = Time(r["mjd"], format="mjd")
        frames.append({
            "id": fid, "obs_id": r["obs_id"], "collection": r["collection"], "url": r["url"],
            "detector": r["detector"], "band": DET_BANDS[r["detector"]],
            "epoch": r["epoch"], "date": t.isot[:19] + "Z", "mjd": round(r["mjd"], 5),
            "wavelength_um": round(lam, 3) if lam else None,
            "width_px": n, "height_px": n, "pixel_scale_arcsec": 6.15,
            "flags": summ,
        })

    # one shared stretch per detector
    limits = {}
    for d in dets:
        vals = [arrays[f["id"]] for f in frames if f["detector"] == d]
        if vals:
            allv = np.concatenate([v[np.isfinite(v)].ravel() for v in vals])
            limits[d] = (float(np.percentile(allv, 5)), float(np.percentile(allv, 99.7)))
    for f in frames:
        lo, hi = limits[f["detector"]]
        f["png"] = save_gray(arrays[f["id"]], lo, hi, f"{key}_{f['id']}.png")

    # stacks per (epoch, detector) and differences between consecutive epochs
    stacks, diffs = [], []
    by = defaultdict(list)
    for f in frames:
        by[(f["epoch"], f["detector"])].append(f)
    stack_arr, stack_full = {}, {}     # stack_full: NaN where not every exposure covers (for colour)
    for (ep, d), fs in sorted(by.items()):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            st = np.array([arrays[f["id"]] for f in fs])
            # cosmic rays / hot pixels sit in one exposure only: min of 2, median of 3+
            s = st[0] if len(st) == 1 else np.nanmin(st, axis=0) if len(st) == 2 else np.nanmedian(st, axis=0)
            partial = (np.isfinite(st).sum(0) < len(st)) & np.isfinite(s)
        big_partial = _outside(np.where(partial, np.nan, 0.0))   # ignore tiny holes, keep star cores
        stack_full[(ep, d)] = np.where(big_partial, np.nan, s)
        stack_arr[(ep, d)] = s
        lo, hi = limits[d]
        mjd = float(np.mean([f["mjd"] for f in fs]))
        stacks.append({
            "epoch": ep, "detector": d, "band": DET_BANDS[d], "n_frames": len(fs),
            "date": Time(mjd, format="mjd").isot[:10],
            "png": save_gray(s, lo, hi, f"{key}_stack_e{ep}_d{d}.png"),
            "streak": any(f["flags"]["streak"] for f in fs),
        })
    color = []
    climits = {}                      # colour: background -> black, per detector
    for d in dets:
        vals = [(lambda f: f - ndimage.median_filter(f, 13))(np.nan_to_num(_fill_holes(v)))[np.isfinite(v)]
                for (ep, dd), v in stack_full.items() if dd == d]
        if vals:
            allv = np.concatenate(vals)
            climits[d] = (float(np.percentile(allv, 65)), float(np.percentile(allv, 99.3)))
    for ep in sorted({ep for (ep, _) in stack_arr}):
        png, used = save_color(stack_full, ep, climits, f"{key}_color_e{ep}.png")
        if png:
            sd = [s for s in stacks if s["epoch"] == ep]
            color.append({"epoch": ep, "detectors": used, "png": png,
                          "date": sd[0]["date"], "n_frames": sum(s["n_frames"] for s in sd),
                          "streak": any(s["streak"] for s in sd)})
    for d in dets:
        eps = sorted(ep for (ep, dd) in stack_arr if dd == d)
        for a, b in zip(eps, eps[1:]):
            diff = _fill_holes(stack_arr[(b, d)]) - _fill_holes(stack_arr[(a, d)])
            lim = float(np.nanpercentile(np.abs(diff), 99.5)) or 1.0
            diffs.append({"detector": d, "from_epoch": a, "to_epoch": b,
                          "png": save_diff(diff, lim, f"{key}_diff_e{a}e{b}_d{d}.png")})

    result = {
        "release": spherex.COLLECTIONS,
        "target": {"name": name, "ra": round(ra, 6), "dec": round(dec, 6), "size_deg": size_deg,
                   "width_px": n},
        "n_images_found": len(rows), "n_epochs": n_epochs,
        "frames": frames, "stacks": stacks, "color": color, "diffs": diffs, "streaks": [],
    }
    result = clean(result)
    cached.write_text(json.dumps(result))
    return result
