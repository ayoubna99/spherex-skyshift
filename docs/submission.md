# Space Apps 2026 submission — SkyShift

Paste each section into the matching field of the submission form.

## Project name

SkyShift

## High-level summary (≈ 150 words)

SPHEREx photographs the whole sky every six months in 102 infrared colours. SkyShift lets anyone
point at a patch of sky and watch what moved through it: stars drifting across the flipbook, stars
brightening and fading, and the satellites crossing the data. SPHEREx's pipeline flags satellite
streaks as unusable pixels. We recover them. Because SPHEREx sees every spot through two cameras at
the same instant, each streak is recorded at two wavelengths at once. Stars in the same images act as
the control. Satellites come out more than 100 times "redder" than the stars, because they glow with
their own heat. Fitting that glow gives each satellite's temperature. Matching the streak against the
satellite catalogue, using SPHEREx's own position from the image header, tells us which object it was
and how far away. Example: a 1986 Japanese rocket stage, 987 km from SPHEREx, at 279 K (about 6 °C).

## Demo link

(30-second video link from Ilyes)

## Final project link

https://spherex.ngdev.org   ·   code: https://github.com/ayoubna99/spherex-skyshift

## Detailed project description

**What it does.** Type a position or pick one of five test objects. SkyShift finds every SPHEREx
exposure of that spot (DR1 and Quick Release 3 at NASA's IRSA archive), puts them on one grid and
plays the six-monthly sky passes as a flipbook. *Difference* mode shows what changed: Barnard's Star,
Epsilon Indi B and the cold brown dwarf WISE 0855 appear as blue–orange pairs as they move; R Draconis
and RW Aurigae change brightness. Any single exposure can then be searched for satellites.

**How the satellite part works.**
1. Find the streaks SPHEREx's pipeline flagged (bit 25 of the FLAGS layer), in both cameras of the
   pair (D1/D4, D2/D5, D3/D6).
2. Measure light just outside the masked core, and convert it to total brightness with the stars in
   the same image.
3. Compare the long-wavelength camera with the short one. Reflected sunlight alone would give
   0.27×; the 1,000+ stars in the same images give 0.24–0.25×; the satellites give 28× and 58×.
4. Fit a blackbody to the 4.4–5.0 µm points: 279 K (268–292) and 294 K (278–311), consistent with
   recent thermal models of satellites in SPHEREx data (Borlaff et al. 2026).
5. Name it: the FITS header gives SPHEREx's position at the start, middle and end of the exposure.
   We propagate ~31,500 catalogued objects with SGP4 across those 115 seconds and keep the one whose
   track lies on the streak and moves the same way. Both showcase streaks matched exactly one object
   (H-1 R/B, NORAD 16910, 987 km away; COSMOS 1980, NORAD 19649, 226 km away). An independent check
   agrees: SPHEREx's own "first half of the exposure" flag matches the crossing times we computed.

**Why it matters.** Satellites are a growing problem for astronomy; SPHEREx scientists throw these
pixels away. SkyShift turns the waste into a measurement: which object, how far, how hot — from a
telescope that was never designed to look at satellites. Temperatures of debris and old rocket
stages are hard to get and relevant to space-situational awareness. And for the public, it turns a
NASA survey into something you can explore: your patch of sky, what moved through it.

**Honest limits.** Ratios depend on method by ±30%; temperatures are stable. One temperature is an
average over the visible surface. Size cannot be separated from emissivity. Naming needs orbit data
for the exposure date and only works for catalogued objects.

**Tools.** Python (FastAPI, Astropy, reproject, NumPy, SciPy, sgp4, Skyfield), plain HTML/JS with
Aladin Lite for the sky map, Google Cloud Run for the backend. A pre-rendered snapshot keeps the demo
fast even if the archive is busy.

## Use of artificial intelligence

We used AI tools to help with coding and research. No image on the site is AI-generated; every image is SPHEREx data.

## NASA data

- SPHEREx Level 2 spectral images, DR1 and Quick Release 3, NASA/IPAC Infrared Science Archive
  (IRSA): https://irsa.ipac.caltech.edu/Missions/spherex.html — accessed through the IRSA SIA2
  search service and image cutout service.
- SPHEREx Explanatory Supplement (flag bits, detectors, wavelength calibration).
- AllWISE colour sky survey (NASA WISE) as the background of the sky map.

## Space agency partner and other data

- Space-Track.org (US Space Force) satellite orbit elements, used under its user agreement and not
  redistributed.
- Aladin Lite v3, CDS Strasbourg (sky map).

## References

- Akeson et al. (2025), The SPHEREx Imaging and Spectrophotometry Processing Pipeline.
- Borlaff et al. (2026), arXiv:2605.27501, satellite trails in SPHEREx exposures.
- SPHEREx acknowledgement: "This publication makes use of data products from the Spectro-Photometer
  for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint
  project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded
  by the National Aeronautics and Space Administration."
