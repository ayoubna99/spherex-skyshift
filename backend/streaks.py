"""Satellites in SPHEREx exposures: find flagged streaks and measure them in both cameras.

Why this works
- SPHEREx splits the light with a dichroic: detectors D1/D4, D2/D5 and D3/D6 see the SAME sky at the
  SAME time, in short-wave (0.75-2.4 um) and long-wave (2.4-5 um) light.
- The pipeline flags satellite streaks (FLAGS bit 25 STREAK). The streak core is corrupted (bit 0
  TRANSIENT, bit 2 SUR_ERROR: the up-the-ramp fit stops when the satellite hits), but the edges of the
  streak ("wings") keep real signal.
- We measure the wing signal, convert it to total streak brightness with a line-spread function built
  from stars in the same image, and compare the two cameras. Stars in the same images are the control.
- In the 4.4-5 um band, a warm satellite's own heat dominates; the slope across that band gives a
  blackbody temperature.
"""
import hashlib
import json
import warnings

import numpy as np
from astropy import log as astropy_log
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning
from PIL import Image
from scipy import ndimage
from scipy.optimize import curve_fit

from . import frames, identify, spherex

astropy_log.setLevel("ERROR")
warnings.simplefilter("ignore", FITSFixedWarning)

FULL = spherex.CACHE / "full"
FULL.mkdir(parents=True, exist_ok=True)
IMG = spherex.CACHE / "img"
STREAK, TRANSIENT, OVERFLOW, SUR_ERROR = 1 << 25, 1 << 0, 1 << 1, 1 << 2
BAD = TRANSIENT | OVERFLOW | SUR_ERROR | (1 << 6) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 15) | (1 << 26) | (1 << 27)
PAIR = {1: 4, 2: 5, 3: 6, 4: 1, 5: 2, 6: 3}
H_C_K = 14387.77          # um K


# ---------------------------------------------------------------- data loading
def partner_url(url, det, other):
    return url.replace(f"/{det}/level2_", f"/{other}/level2_").replace(f"D{det}_spx", f"D{other}_spx")


def fetch_full(url):
    p = FULL / (hashlib.md5(url.encode()).hexdigest() + ".fits")
    if not p.exists():
        data = spherex._get(url, timeout=400)
        if not data.startswith(b"SIMPLE"):
            raise RuntimeError("full image download failed")
        tmp = p.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.rename(p)
    return p


class Exposure:
    """One full detector image: image minus zodiacal model, flags, sky and wavelength coordinates."""

    def __init__(self, path):
        with fits.open(path) as h:
            hdr = h["IMAGE"].header
            self.img = h["IMAGE"].data.astype(np.float32)
            if "ZODI" in h:
                self.img -= h["ZODI"].data
            self.flags = h["FLAGS"].data.astype(np.int64)  # bit 29 needs > int16; kept simple
            self.wcs = WCS(hdr)
            self.wave = WCS(hdr, fobj=h, key="W")
            self.date = hdr.get("DATE-OBS")
            self.det = hdr.get("DETECTOR")
            self.eph = {k: hdr.get(k) for k in identify.EPH_KEYS}
        self.ny, self.nx = self.img.shape
        self.Y, self.X = np.mgrid[0:self.ny, 0:self.nx].astype(np.float32)
        self._resid = None

    @property
    def resid(self):
        """Image minus a smooth background (block medians, fast), computed once."""
        if self._resid is None:
            a = np.nan_to_num(self.img).astype(np.float32)
            b = 24
            ny, nx = self.ny // b * b, self.nx // b * b
            blocks = np.median(a[:ny, :nx].reshape(ny // b, b, nx // b, b), axis=(1, 3))
            bg = ndimage.zoom(blocks, b, order=1)
            full = np.zeros_like(a)
            full[:ny, :nx] = bg
            full[ny:, :] = full[ny - 1:ny, :]
            full[:, nx:] = full[:, nx - 1:nx]
            self._resid = a - full
        return self._resid

    def wavelength(self, x, y):
        return float(self.wave.pixel_to_world_values(x, y)[0])


# ---------------------------------------------------------------- streak geometry
def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _fit_line(pts):
    c = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    d = vt[0]
    for _ in range(3):
        n = np.array([-d[1], d[0]])
        k = np.abs((pts - c) @ n) < 3
        if k.sum() < 20:
            break
        c = pts[k].mean(0)
        _, _, vt = np.linalg.svd(pts[k] - c, full_matrices=False)
        d = vt[0]
    n = np.array([-d[1], d[0]])
    t = (pts - c) @ d
    return {"c": c, "d": d, "n": n, "t0": float(t.min()), "t1": float(t.max()), "npx": len(pts)}


def find_lines(exp, min_px=150):
    """Straight streaks from the STREAK flag: connected pieces, line fit, merge collinear pieces."""
    m = (exp.flags & STREAK) > 0
    lab, nlab = ndimage.label(ndimage.binary_dilation(m, iterations=2))
    pieces = []
    for i in range(1, nlab + 1):
        ys, xs = np.nonzero((lab == i) & m)
        if len(xs) >= min_px:
            pieces.append(np.c_[xs, ys].astype(float))
    lines = []
    for pts in sorted(pieces, key=len, reverse=True):
        L = _fit_line(pts)
        merged = False
        for M in lines:
            ang = abs(np.degrees(np.arcsin(np.clip(abs(_cross(M["d"], L["d"])), 0, 1))))
            if ang < 2 and abs((L["c"] - M["c"]) @ M["n"]) < 5:
                M.update(_fit_line(np.vstack([M["pts"], pts])))
                M["pts"] = np.vstack([M["pts"], pts])
                merged = True
                break
        if not merged:
            L["pts"] = pts
            lines.append(L)
    return [L for L in lines if L["t1"] - L["t0"] > 80]   # at least 80 px long


def line_to_sky(exp, L):
    p = np.array([L["c"] + L["d"] * L["t0"], L["c"] + L["d"] * L["t1"]])
    return exp.wcs.pixel_to_world_values(p[:, 0], p[:, 1])


def sky_to_line(exp, ra, dec):
    x, y = exp.wcs.world_to_pixel_values(np.asarray(ra), np.asarray(dec))
    p0, p1 = np.array([x[0], y[0]]), np.array([x[1], y[1]])
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    c = (p0 + p1) / 2
    half = np.linalg.norm(p1 - p0) / 2
    return {"c": c, "d": d, "n": np.array([-d[1], d[0]]), "t0": -half, "t1": half}


def clip_to_image(exp, L, margin=12):
    t = np.linspace(L["t0"], L["t1"], 2000)
    pts = L["c"] + np.outer(t, L["d"])
    ok = (pts[:, 0] > margin) & (pts[:, 0] < exp.nx - margin) & (pts[:, 1] > margin) & (pts[:, 1] < exp.ny - margin)
    if ok.sum() < 50:
        return None
    L = dict(L)
    L["t0"], L["t1"] = float(t[ok].min()), float(t[ok].max())
    return L


# ---------------------------------------------------------------- photometry
def line_spread(exp, n, exclude, R=10, nmax=500):
    """Median-stacked star profile projected perpendicular to the streak (normalised to sum 1)."""
    s = exp.resid
    noise = 1.4826 * np.nanmedian(np.abs(s))
    mx = ndimage.maximum_filter(s, size=7)
    cand = (s == mx) & (s > 15 * noise) & (s < 3000 * noise) & ((exp.flags & BAD) == 0) & ~exclude
    ys, xs = np.nonzero(cand)
    ok = (ys > R + 2) & (ys < exp.ny - R - 3) & (xs > R + 2) & (xs < exp.nx - R - 3)
    ys, xs = ys[ok], xs[ok]
    order = np.argsort(-s[ys, xs])
    bins = np.arange(-R, R + 0.51, 0.5)
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    rows = []
    for i in order:
        y, x = ys[i], xs[i]
        cut, F = s[y - R:y + R + 1, x - R:x + R + 1], exp.flags[y - R:y + R + 1, x - R:x + R + 1]
        if np.any((F & (BAD | STREAK)) != 0):
            continue
        c2 = cut.copy()
        c2[R - 2:R + 3, R - 2:R + 3] = 0
        if c2.max() > 0.15 * cut[R, R]:
            continue
        w = np.clip(cut, 0, None)
        cy, cx = (w * yy).sum() / w.sum(), (w * xx).sum() / w.sum()
        core = cut[np.hypot(yy - cy, xx - cx) < 2.5].sum()
        if core <= 0:
            continue
        p = (xx - cx) * n[0] + (yy - cy) * n[1]
        h, _ = np.histogram(p.ravel(), bins=bins, weights=(cut / core).ravel())
        rows.append(h)
        if len(rows) >= nmax:
            break
    if len(rows) < 15:
        return None
    L = np.median(rows, 0)
    L /= L.sum()
    pc = 0.5 * (bins[1:] + bins[:-1])
    return float(L[(np.abs(pc) >= 3) & (np.abs(pc) <= 8)].sum())


def wing_segments(exp, L, seg=160):
    """Background-subtracted wing signal (3-8 px from the line) per segment along the streak."""
    out = []
    t = L["t0"]
    while t + seg / 2 < L["t1"]:
        lo, hi = t, min(t + seg, L["t1"])
        pts = np.array([L["c"] + L["d"] * lo, L["c"] + L["d"] * hi])
        x0, x1 = int(max(0, pts[:, 0].min() - 16)), int(min(exp.nx, pts[:, 0].max() + 16))
        y0, y1 = int(max(0, pts[:, 1].min() - 16)), int(min(exp.ny, pts[:, 1].max() + 16))
        Y, X = np.mgrid[y0:y1, x0:x1]
        I, F = exp.img[y0:y1, x0:x1], exp.flags[y0:y1, x0:x1]
        perp = (X - L["c"][0]) * L["n"][0] + (Y - L["c"][1]) * L["n"][1]
        along = (X - L["c"][0]) * L["d"][0] + (Y - L["c"][1]) * L["d"][1]
        ok = (along >= lo) & (along < hi) & np.isfinite(I) & ((F & (TRANSIENT | SUR_ERROR)) == 0)
        bgsel = ok & (np.abs(perp) > 9) & (np.abs(perp) < 14)
        if bgsel.sum() < 20:
            t += seg
            continue
        bg = np.nanmedian(I[bgsel])
        noise = 1.4826 * np.nanmedian(np.abs(I[bgsel] - bg))
        prof, nb = [], 0
        for b in np.arange(3, 8.01, 0.5):
            for sgn in (-1, 1):
                sel = ok & (np.abs(perp * sgn - b - 0.25) < 0.25)
                if sel.sum() > 5:
                    prof.append(np.nanmedian(I[sel]) - bg)   # median rejects stars on the streak
                    nb += sel.sum()
        if len(prof) < 10:
            t += seg
            continue
        mid = L["c"] + L["d"] * (lo + hi) / 2
        out.append({
            "x": float(mid[0]), "y": float(mid[1]),
            "um": exp.wavelength(mid[0], mid[1]),
            "wing": float(np.sum(prof) * 0.5),
            "err": float(noise * 0.5 * np.sqrt(len(prof)) / np.sqrt(max(nb / len(prof), 1)) * 1.25),
        })
        t += seg
    return out


def star_ratio(short, long_):
    """Median long/short flux ratio of stars seen in both cameras (control: ~0.2-0.3 for normal stars)."""
    s = short.resid
    noise = 1.4826 * np.median(np.abs(s))
    mx = ndimage.maximum_filter(s, size=9)
    ys, xs = np.nonzero((s == mx) & (s > 50 * noise) & (s < 1500 * noise))
    ok = (ys > 30) & (ys < short.ny - 30) & (xs > 30) & (xs < short.nx - 30)
    ys, xs = ys[ok][:2500], xs[ok][:2500]
    ra, dec = short.wcs.pixel_to_world_values(xs, ys)
    xl, yl = long_.wcs.world_to_pixel_values(ra, dec)

    def phot(e, x, y, r=3):
        X0, Y0 = int(round(x)), int(round(y))
        if not (15 < X0 < e.nx - 15 and 15 < Y0 < e.ny - 15):
            return np.nan
        yy, xx = np.mgrid[Y0 - 12:Y0 + 13, X0 - 12:X0 + 13]
        I, F = e.img[Y0 - 12:Y0 + 13, X0 - 12:X0 + 13], e.flags[Y0 - 12:Y0 + 13, X0 - 12:X0 + 13]
        rr = np.hypot(xx - x, yy - y)
        if np.any((F[rr < r + 1] & (BAD | STREAK)) != 0) or np.any(~np.isfinite(I)):
            return np.nan
        return np.nansum(I[rr < r] - np.nanmedian(I[(rr > 7) & (rr < 11)]))

    rat = []
    for x, y, a, b in zip(xs, ys, xl, yl):
        A, B = phot(short, float(x), float(y)), phot(long_, float(a), float(b))
        if np.isfinite(A) and np.isfinite(B) and A > 0 and B > 0:
            rat.append(B / A)
    return (float(np.median(rat)), len(rat)) if len(rat) >= 30 else (None, len(rat))


# ---------------------------------------------------------------- physics
def bnu(lam, T):
    return lam ** -3 / np.expm1(H_C_K / (lam * T))


def sun(lam):
    return bnu(lam, 5772.0)


def fit_temperature(seg_long, rng=np.random.default_rng(1)):
    """Blackbody fit to the long-wave segments (only meaningful at 3.8-5 um). Returns (T, lo, hi)."""
    lam = np.array([s["um"] for s in seg_long])
    F = np.array([s["flux"] for s in seg_long])
    E = np.array([s["flux_err"] for s in seg_long]) + 0.05 * np.abs(F)
    if len(lam) < 4 or lam.min() < 3.7 or np.median(F / E) < 4:
        return None
    f = lambda l, b, T: b * bnu(l, T) / bnu(4.7, T)
    try:
        p, _ = curve_fit(f, lam, F, p0=(np.median(F), 300), sigma=E, bounds=([0, 120], [np.inf, 900]))
    except Exception:            # noqa: BLE001
        return None
    Ts = []
    for _ in range(300):
        k = rng.integers(0, len(lam), len(lam))
        try:
            Ts.append(curve_fit(f, lam[k], F[k], p0=p, sigma=E[k], bounds=([0, 120], [np.inf, 900]))[0][1])
        except Exception:        # noqa: BLE001
            pass
    if len(Ts) < 50:
        return None
    lo, hi = np.percentile(Ts, [16, 84])
    return float(p[1]), float(lo), float(hi)


# ---------------------------------------------------------------- images for the card
def crop_png(exp, L, name, half=60):
    mid = L["c"] + L["d"] * (L["t0"] + L["t1"]) / 2
    x0, y0 = int(mid[0]) - half, int(mid[1]) - half
    x0, y0 = max(0, min(exp.nx - 2 * half, x0)), max(0, min(exp.ny - 2 * half, y0))
    a = exp.img[y0:y0 + 2 * half, x0:x0 + 2 * half]
    a = np.nan_to_num(a - np.nanmedian(a))
    lo, hi = np.percentile(a, 2), np.percentile(a, 99.5)
    s = 0.05 * (hi - lo) if hi > lo else 1.0
    v = np.clip(np.arcsinh((a - lo) / s) / np.arcsinh((hi - lo) / s), 0, 1)
    Image.fromarray((v[::-1] * 255).astype(np.uint8)).resize((240, 240), Image.NEAREST).save(IMG / name)
    return f"img/{name}"


# ---------------------------------------------------------------- satellite naming
def _name_it(eph, line_sky):
    """Match the streak against the satellite catalogue (only if an orbit file is installed)."""
    try:
        cands = identify.identify(eph, line_sky)
    except Exception as e:                           # naming is a bonus; never break the measurement
        print("identify failed:", e)
        cands = []
    best = cands[0] if cands else None
    return {"satellite": best, "satellite_candidates": cands,
            "distance_km": best["distance_km"] if best else None,
            "catalog_checked": identify.tle_path() is not None}


# ---------------------------------------------------------------- main entry
def analyse(url, det):
    """Find and measure every satellite streak in one exposure pair. Cached per exposure."""
    other = PAIR[det]
    s_det, l_det = min(det, other), max(det, other)
    urls = {det: url, other: partner_url(url, det, other)}
    key = hashlib.md5(f"v3|{urls[s_det]}".encode()).hexdigest()[:12]
    cache = spherex.CACHE / f"streaks_{key}.json"
    if cache.exists():
        return frames.clean(json.loads(cache.read_text()))

    short = Exposure(fetch_full(urls[s_det]))
    long_ = Exposure(fetch_full(urls[l_det]))

    # streak lines from BOTH cameras (a satellite may be flagged in only one), in sky coordinates
    sky_lines = [line_to_sky(short, L) for L in find_lines(short)] + [line_to_sky(long_, L) for L in find_lines(long_)]
    uniq = []
    for ra, dec in sky_lines:
        Ls = sky_to_line(short, ra, dec)
        dup = False
        for ra2, dec2 in uniq:
            M = sky_to_line(short, ra2, dec2)
            ang = abs(np.degrees(np.arcsin(np.clip(abs(_cross(M["d"], Ls["d"])), 0, 1))))
            if ang < 2 and abs((Ls["c"] - M["c"]) @ M["n"]) < 6:
                dup = True
                break
        if not dup:
            uniq.append((ra, dec))

    sr, n_stars = star_ratio(short, long_)
    cards = []
    for i, (ra, dec) in enumerate(uniq):
        res = {}
        for tag, exp in (("short", short), ("long", long_)):
            L = clip_to_image(exp, sky_to_line(exp, ra, dec))
            if L is None:
                break
            near = np.abs((exp.X - L["c"][0]) * L["n"][0] + (exp.Y - L["c"][1]) * L["n"][1]) < 30
            fw = line_spread(exp, L["n"], near)
            segs = wing_segments(exp, L)
            if fw is None or not segs:
                break
            for s in segs:
                s["flux"], s["flux_err"] = s["wing"] / fw, s["err"] / fw
            res[tag] = {"L": L, "segs": segs, "fw": fw, "exp": exp}
        if len(res) < 2:
            continue
        fs = np.median([s["flux"] for s in res["short"]["segs"]])
        fl = np.median([s["flux"] for s in res["long"]["segs"]])
        es = np.median([s["flux_err"] for s in res["short"]["segs"]]) / np.sqrt(len(res["short"]["segs"]))
        el = np.median([s["flux_err"] for s in res["long"]["segs"]]) / np.sqrt(len(res["long"]["segs"]))
        if fl < 3 * el and fs < 3 * es:
            continue                                  # too faint in both cameras
        lam_s = np.median([s["um"] for s in res["short"]["segs"]])
        lam_l = np.median([s["um"] for s in res["long"]["segs"]])
        ratio = float(fl / fs) if fs > 3 * es else None
        temp = fit_temperature(res["long"]["segs"]) if l_det >= 5 else None
        Ls = res["short"]["L"]
        frac_first = float(((short.flags & SUR_ERROR) > 0)[(short.flags & STREAK) > 0].mean()) \
            if ((short.flags & STREAK) > 0).any() else None
        sid = f"{key}_s{i}"
        cards.append({
            "id": sid,
            "date": short.date,
            "detectors": [s_det, l_det],
            "line_sky": [[float(ra[0]), float(dec[0])], [float(ra[1]), float(dec[1])]],
            "length_deg": float(np.degrees(np.arccos(np.clip(
                np.sin(np.radians(dec[0])) * np.sin(np.radians(dec[1])) + np.cos(np.radians(dec[0]))
                * np.cos(np.radians(dec[1])) * np.cos(np.radians(ra[0] - ra[1])), -1, 1)))),
            "flux_short": float(fs), "flux_long": float(fl),
            "wavelength_short_um": float(lam_s), "wavelength_long_um": float(lam_l),
            "ratio_long_short": ratio,
            "ratio_stars": sr, "n_stars": n_stars,
            "ratio_reflected_only": float(sun(lam_l) / sun(lam_s)),
            "temp_k": round(temp[0]) if temp else None,
            "temp_range_k": [round(temp[1]), round(temp[2])] if temp else None,
            "spectrum": [{"um": round(s["um"], 3), "value": round(s["flux"], 3), "err": round(s["flux_err"], 3),
                          "camera": tag}
                         for tag in ("short", "long") for s in res[tag]["segs"]],
            "png_short": crop_png(short, Ls, f"{sid}_short.png"),
            "png_long": crop_png(long_, res["long"]["L"], f"{sid}_long.png"),
            "time_half_hint": None if frac_first is None else ("first" if frac_first > 0.5 else "second"),
            **_name_it(short.eph, [[float(ra[0]), float(dec[0])], [float(ra[1]), float(dec[1])]]),
        })
    out = {"key": key, "date": short.date, "mjd": Time(short.date).mjd if short.date else None,
           "detectors": [s_det, l_det], "n_streaks_flagged": len(uniq), "streaks": cards}
    out = frames.clean(out)
    cache.write_text(json.dumps(out))
    return out
