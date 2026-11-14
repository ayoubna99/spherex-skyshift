"""Pilot census: run SkyShift's two-camera streak photometry on Borlaff et al. (2026) catalogued trails."""
import csv, json, random, sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import spherex, streaks

OUT = Path("census/results.jsonl")
DONE = {json.loads(l)["OBSID"] for l in OUT.open()} if OUT.exists() else set()
rows = list(csv.DictReader(open("census/SPHEREx_trails_database.csv")))
random.Random(42).shuffle(rows)                      # unbiased sample order, reproducible
budget_s = float(sys.argv[1]) if len(sys.argv) > 1 else 3600 * 5
t_start = time.time()
for t in rows:
    if time.time() - t_start > budget_s:
        break
    if t["OBSID"] in DONE:
        continue
    rec = {"OBSID": t["OBSID"], "ra": float(t["ra"]), "dec": float(t["dec"]), "pa": float(t["pa"]), "mjd": float(t["MJDOBS"])}
    try:
        hits = [h for h in spherex.search(rec["ra"], rec["dec"], 0.02) if h["obs_id"] == t["OBSID"]]
        dets = sorted(h["detector"] for h in hits)
        rec["detectors_covering"] = dets
        pair = 3 if 3 in dets and 6 in dets else 2 if 2 in dets and 5 in dets else None
        if pair is None:
            rec["skipped"] = "not on a long-wave pair (D2/D5 or D3/D6)"
        else:
            url = [h for h in hits if h["detector"] == pair][0]["url"]
            t0 = time.time()
            r = streaks.analyse(url, pair)
            rec["seconds"] = round(time.time() - t0)
            rec["n_streaks_flagged"] = r["n_streaks_flagged"]
            rec["streaks"] = [{k: v for k, v in s.items() if k not in ("spectrum", "satellite_candidates")} | {"spectrum": s["spectrum"]} for s in r["streaks"]]
            # free the ~130 MB of full frames
            for f in (streaks.FULL).glob("*.fits"):
                f.unlink()
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    with OUT.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(time.strftime("%H:%M:%S"), rec["OBSID"], rec.get("detectors_covering"), rec.get("skipped") or rec.get("error") or f"flagged={rec.get('n_streaks_flagged')} measured={len(rec.get('streaks', []))} in {rec.get('seconds')}s", flush=True)
