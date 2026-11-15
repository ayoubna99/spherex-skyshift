"""Find SPHEREx images at a sky position (IRSA SIA) and download small cutouts (IRSA cutout service).

Data: NASA SPHEREx spectral images from the NASA/IPAC Infrared Science Archive (IRSA).
Collections are listed in COLLECTIONS so new releases are a one-line change.
"""
import csv
import hashlib
import io
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

COLLECTIONS = ["spherex_dr1", "spherex_qr3"]   # add new releases here (e.g. "spherex_dr2")
SIA = "https://irsa.ipac.caltech.edu/SIA"
CACHE = Path(__file__).parent / "cache"
CUTOUTS = CACHE / "cutouts"
CUTOUTS.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "SkyShift/0.1 (NASA Space Apps 2026)"}


def _get(url, timeout=120, tries=6):
    """GET with retries: IRSA answers 'Server busy' (HTTP 503) under load."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:          # noqa: BLE001 - retry any network error
            last = e
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
    raise RuntimeError(f"IRSA request failed after {tries} tries: {last}")


SKIPPED = {}   # collection -> last error, for the most recent search (shown to the user as a note)


def _search_one(col, ra, dec, radius_deg):
    q = urllib.parse.urlencode({"COLLECTION": col, "RESPONSEFORMAT": "CSV"})
    url = f"{SIA}?{q}&POS=circle+{ra:.6f}+{dec:.6f}+{radius_deg}"
    cache = CACHE / f"sia_{hashlib.md5(url.encode()).hexdigest()}.csv"
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        text = cache.read_text()
    else:
        text = _get(url, timeout=120, tries=3).decode()   # fail fast: one bad collection must not stall the rest
        cache.write_text(text)
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("dataproduct_subtype") != "science" or not r.get("access_url"):
            continue
        rows.append({
            "collection": col,
            "obs_id": r["obs_id"],
            "detector": int(r["energy_bandpassname"][-1]),
            "mjd": float(r["t_min"]),
            "url": r["access_url"],
            "em_min_um": float(r["em_min"]) * 1e6 if r.get("em_min") else None,
            "em_max_um": float(r["em_max"]) * 1e6 if r.get("em_max") else None,
        })
    return rows


def search(ra, dec, radius_deg=0.01):
    """All SPHEREx images whose footprint contains (ra, dec), from every collection. The collections
    are searched in parallel; if one of IRSA's services is down, the others are still used."""
    rows, errors = [], {}
    with ThreadPoolExecutor(len(COLLECTIONS)) as ex:
        futs = {col: ex.submit(_search_one, col, ra, dec, radius_deg) for col in COLLECTIONS}
        for col, f in futs.items():
            try:
                rows += f.result()
            except Exception as e:      # noqa: BLE001
                errors[col] = str(e)[:200]
    SKIPPED.clear(); SKIPPED.update(errors)
    if not rows and errors:
        raise RuntimeError("NASA's SPHEREx archive (IRSA) is not answering right now: " + "; ".join(errors.values()))
    rows.sort(key=lambda r: r["mjd"])
    return rows


def cutout_path(row, ra, dec, size_deg):
    key = f"{row['url']}|{ra:.5f}|{dec:.5f}|{size_deg:.4f}"
    return CUTOUTS / f"{hashlib.md5(key.encode()).hexdigest()}.fits"


def fetch_cutout(row, ra, dec, size_deg):
    """Download one cutout (cached on disk). Returns the local path."""
    p = cutout_path(row, ra, dec, size_deg)
    if not p.exists():
        url = f"{row['url']}?center={ra:.6f},{dec:.6f}&size={size_deg:.4f}"
        data = _get(url)
        if not data.startswith(b"SIMPLE"):
            raise RuntimeError(f"not a FITS file: {data[:80]!r}")
        tmp = p.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.rename(p)
    return p


def fetch_many(rows, ra, dec, size_deg, workers=6):
    """Download cutouts in parallel; returns {index: path} for those that succeeded."""
    out = {}

    def job(i):
        try:
            return i, fetch_cutout(rows[i], ra, dec, size_deg)
        except Exception:               # noqa: BLE001 - skip a frame rather than fail the request
            return i, None

    with ThreadPoolExecutor(workers) as ex:
        for i, p in ex.map(job, range(len(rows))):
            if p is not None:
                out[i] = p
    return out
