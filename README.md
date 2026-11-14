# SkyShift — what moved through your patch of sky?

**NASA Space Apps Challenge 2026 · Planet X and SPHEREx**

Point at any patch of sky and see what moved through it in NASA SPHEREx images — stars, and the
satellites polluting the data — and, for the satellites, how hot they are.

**Live site:** https://spherex.ngdev.org

## What it does

1. **Flipbook.** For any position, SkyShift finds every SPHEREx exposure that covers it (DR1 + Quick
   Release 3 at IRSA), cuts out the field, puts all exposures on one north-up grid and plays the
   six-monthly sky passes as a flipbook, with one brightness scale so changes are real.
   *Difference* mode subtracts passes: a moving star (Barnard's Star, Epsilon Indi B, WISE 0855)
   appears as a blue–orange pair; a variable star (R Draconis, RW Aurigae) brightens or fades.
2. **Satellite streaks.** The SPHEREx pipeline flags satellite streaks so scientists can mask them.
   SPHEREx sees each spot through two cameras at the same instant (a short-wavelength camera and a
   long-wavelength camera behind a dichroic), so every streak is recorded twice, at e.g. 2.0 µm and
   4.7 µm. We measure the light at the streak's edges, convert it to total brightness using the
   stars in the same image, and compare the two cameras. Stars in the same images are the control.
3. **Temperature.** A sunlit satellite that only reflected light would be ~0.27× as bright in the long
   camera as in the short one (the Sun's colour). Stars come out at 0.24–0.25×. Satellites come out
   at 28–58× — they glow in the infrared. A blackbody fit to the 4.4–5.0 µm points gives the
   temperature, with a bootstrap range.
4. **Which satellite.** The image header gives SPHEREx's own position during the exposure. We
   propagate every catalogued object (Space-Track elements, SGP4) across the exposure and keep the
   object whose apparent track lies on the streak and moves the same way. That gives the name and the
   distance.

5. **Roughly how big.** Thermal flux = emissivity × area × Planck(T) / distance². With the fitted
   temperature and the matched distance, the streak brightness (corrected for the crossing rate and the
   exposure length from the header, emissivity assumed 0.9) gives a thermal cross-section. H-1 stage:
   ~90 m² (55–141 over the temperature range) against a published 10.32 m × 2.49 m cylinder (~26 m²
   side-on). COSMOS 1980: ~32 m² (18–58) against a 4.5 m × 1.4 m bus with a 22.7 m solar-panel span.
   Order-of-magnitude agreement with no albedo assumption; a factor-of-few check, not a precision size.

6. **Pilot census.** `scripts/run_census.py` draws a seeded random sample from the 2,182 trails NASA Ames
   identified by eye (Borlaff et al. 2026, Zenodo 10.5281/zenodo.17957748), fetches the DR1 reprocessing of
   each exposure from IRSA and runs the same two-camera photometry with no hand-picking;
   `scripts/build_census.py` aggregates into `frontend/static/census.json` (the site draws the
   distributions). Rules: a trail counts as measured only with a >3σ long-camera detection; the long/short
   ratio is a lower limit when the short camera sees nothing; temperatures are kept only when the bootstrap
   range is under 100 K. Results so far are in `census/results.jsonl`. To extend: `python scripts/run_census.py 7200`
   (seconds of budget), then `python scripts/build_census.py`.

### Showcase results (near Barnard's Star, August 2026)

| Exposure | Object | Distance from SPHEREx | Temperature | Long/short ratio (stars) |
| --- | --- | --- | --- | --- |
| 2026W34_1A_0238_2, D3+D6 | H-1 R/B (MABES), NORAD 16910 — Japanese rocket stage, 1986 | 987 km | 279 K (268–292) | 58× (0.25×) |
| 2026W34_1A_0431_2, D3+D6 | COSMOS 1980, NORAD 19649 — Soviet satellite, 1988 | 226 km | 294 K (278–311) | 28× (0.24×) |

Each streak matched exactly one of ~31,500 catalogued objects: track on the streak within
0.04° / 0.07°, direction within 0.02° / 0.14°. Independently, SPHEREx's own flags put both crossings
in the first half of the exposure, and the orbit match puts them 27 s and 22 s into the 115 s
exposure.

### Limits (honest version)

- The ratio depends on the measurement method by ±30% or more; the temperature is much more stable.
  We show ratios as indicative and temperatures with ranges.
- One temperature is an average over the visible surface. The cross-section assumes an emissivity
  (0.9) and inherits the wing-calibration systematic: good to a factor of 2–3, no better.
- Naming needs an orbit file from Space-Track for the dates of the exposure. Objects not in the
  public catalogue cannot be named.
- Temperature needs the long camera (D5/D6, 3.8–5 µm) to see the streak.

## Structure

| Folder | What |
| --- | --- |
| `backend/` | FastAPI: image search (`spherex.py`), flipbook (`frames.py`), streak photometry and temperature (`streaks.py`), satellite naming (`identify.py`) |
| `frontend/` | Static web app (HTML/CSS/JS, Aladin Lite sky map). `frontend/static/` holds a pre-rendered snapshot of the demo targets and showcase |
| `scripts/export_static.py` | Rebuilds that snapshot |
| `docs/api.md` | API contract |
| `mock/` | Synthetic data used to build the frontend before the backend existed |

## Run it

```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload                 # API on http://localhost:8000
cd frontend && python -m http.server 8080        # site on http://localhost:8080/?api=http://localhost:8000
```

Optional, for satellite names: download 3-line elements for the exposure dates from Space-Track
(class `gp_history`) and set `SKYSHIFT_TLE=/path/to/file.txt` (or put it in `backend/data/`).
The file is not in this repository: Space-Track data may not be redistributed.

Deploy: the backend runs on Render's free plan (build `pip install -r requirements.txt`, start
`uvicorn backend.app:app --host 0.0.0.0 --port $PORT`). Shrink the orbit file first
(`python -m scripts.slim_tle big.txt slim.txt && gzip -9 slim.txt`), add it as a Render *secret
file* and set `SKYSHIFT_TLE=/etc/secrets/<name>.txt.gz`; one streak analysis with naming then peaks
around 420 MB. The `Dockerfile` works on any container host. The frontend is plain static files; the demo targets and
showcase are pre-rendered in `frontend/static/` and need no backend.

## Data, tools and licenses

**NASA data**
- SPHEREx Level 2 spectral images, DR1 and Quick Release 3, from the NASA/IPAC Infrared Science
  Archive (IRSA), via the IRSA SIA2 and cutout services. DOI for QR2: 10.26131/IRSA652.
  *"This publication makes use of data products from the Spectro-Photometer for the History of the
  Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet
  Propulsion Laboratory and the California Institute of Technology, and is funded by the National
  Aeronautics and Space Administration."*
- This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by NASA and
  operated by the California Institute of Technology.
- Sky map background: AllWISE colour HiPS (NASA WISE), served by CDS.

**Other data**
- Satellite orbits: Space-Track.org (US Space Force), used under its user agreement, not
  redistributed. Object pages linked from the site: N2YO.

**Software**
- Aladin Lite v3 (CDS, GPL-3.0), loaded from the CDS CDN.
- Astropy, reproject, NumPy, SciPy (BSD-3-Clause); FastAPI, Uvicorn, sgp4, Skyfield (MIT);
  Pillow (MIT-CMU).

**References**
- Akeson et al. (2025), The SPHEREx Imaging and Spectrophotometry Processing Pipeline; SPHEREx
  Explanatory Supplement (IRSA).
- Borlaff et al. (2026), arXiv:2605.27501 — satellite trails in SPHEREx exposures and a thermal
  model predicting ~280 K satellites. Our measured temperatures are consistent with it.
- Object dimensions for the size check: Encyclopedia Astronautica (H-1 / LE-5 second stage, 10.32 m ×
  2.49 m); KeepTrack.space catalogue entries for NORAD 16910 and 19649 (Tselina-2: 4.5 m × 1.4 m, 22.7 m span).
- Prior infrared photometry of satellites: WISE geostationary-satellite streak studies; AMOS
  visible + thermal-IR satellite characterisation.

## Use of AI

We used an AI assistant (Claude, Anthropic) as a coding and research partner: brainstorming,
reading documentation and papers, writing and debugging code, and drafting text. All results were
run on real SPHEREx data and checked by the team (control stars, two independent timing checks for
the satellite matches). No AI-generated images are used; every image on the site is SPHEREx data.

## Team

Ayoub (data, backend), Abdou (frontend), Ilyes (video and media).

## License

MIT — see `LICENSE`.
