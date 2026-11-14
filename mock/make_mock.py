"""Generate FAKE frames + JSON so the frontend can be built before the real backend is ready.

Everything produced here is synthetic test data (clearly labelled "mock"). It only mimics the
shape of the real API response described in docs/api.md.
Run:  python mock/make_mock.py
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).parent
IMG = OUT / "img"
IMG.mkdir(exist_ok=True)

N = 200            # image size in pixels
rng = np.random.default_rng(42)
stars = [(rng.uniform(5, N - 5), rng.uniform(5, N - 5), rng.uniform(0.2, 1.0)) for _ in range(60)]
yy, xx = np.mgrid[0:N, 0:N]


def star(img, x, y, amp, sigma=1.2):
    img += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))


frames_meta = [
    ("mock_2025W20_0001_D3", "2025-05-20T10:00:00Z", 2.05),
    ("mock_2025W46_0002_D3", "2025-11-14T10:00:00Z", 2.07),
    ("mock_2026W20_0003_D3", "2026-05-19T10:00:00Z", 2.04),
    ("mock_2026W34_0004_D3", "2026-08-18T04:33:47Z", 2.06),
]

frames = []
for i, (fid, date, lam) in enumerate(frames_meta):
    img = np.zeros((N, N))
    for (x, y, a) in stars:
        star(img, x, y, a)
    star(img, 80 + 6 * i, 100 - 3 * i, 1.5)                    # a "moving star"
    star(img, 150, 50, 0.4 + 0.5 * (i % 2))                    # a "variable star"
    if i == 3:                                                 # a satellite streak in the last frame
        for t in np.linspace(0, 1, 400):
            x, y = 10 + 180 * t, 180 - 150 * t
            star(img, x, y, 0.08, sigma=1.0)
    img += rng.normal(0, 0.03, img.shape)
    png = (np.clip(img / 1.2, 0, 1) * 255).astype(np.uint8)    # one shared stretch for all frames
    Image.fromarray(png[::-1]).save(IMG / f"{fid}.png")
    frames.append({
        "id": fid,
        "obs_id": fid.rsplit("_", 1)[0],
        "detector": 3,
        "date": date,
        "wavelength_um": lam,
        "png": f"mock/img/{fid}.png",
        "width_px": N,
        "height_px": N,
        "pixel_scale_arcsec": 6.15,
        "flags": {"streak": i == 3, "transient_px": 312 if i == 3 else 0},
    })

data = {
    "mock": True,
    "release": "QR3",
    "target": {"name": "Barnard's Star", "ra": 269.452, "dec": 4.693, "size_deg": 0.34},
    "frames": frames,
    "streaks": [{
        "id": "s1",
        "frame_id": frames[3]["id"],
        "partner_frame_id": frames[3]["id"].replace("_D3", "_D6"),
        "line_px": [[10, 20], [190, 170]],
        "time_half": "first",
        "temp_k": 270,
        "temp_range_k": [250, 300],
        "ratio_long_short": 60,
        "ratio_stars": 0.24,
        "satellite": None,
        "distance_km": None,
        "spectrum": [
            {"um": 4.45, "value": 12.0, "err": 1.0},
            {"um": 4.65, "value": 16.0, "err": 1.0},
            {"um": 4.85, "value": 21.0, "err": 1.2},
        ],
    }],
}
(OUT / "frames.json").write_text(json.dumps(data, indent=2))
print("wrote", OUT / "frames.json", "and", len(frames), "PNGs")
