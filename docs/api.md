# API contract (backend ↔ frontend)

The frontend builds against `mock/frames.json` first. The real backend returns the same shape.

## `GET /api/frames?ra=<deg>&dec=<deg>&size_deg=<deg>&release=QR3`

Returns all SPHEREx frames covering the position, oldest first, rendered as PNG with **one shared
brightness stretch** (so brightness changes between frames are real, not display artefacts).

```json
{
  "release": "QR3",
  "target": {"name": "Barnard's Star", "ra": 269.452, "dec": 4.693, "size_deg": 0.34},
  "frames": [
    {
      "id": "2026W34_1A_0238_2D3",
      "obs_id": "2026W34_1A_0238_2",
      "detector": 3,
      "date": "2026-08-18T04:33:47Z",
      "wavelength_um": 2.05,
      "png": "/img/2026W34_1A_0238_2D3.png",
      "width_px": 56,
      "height_px": 56,
      "pixel_scale_arcsec": 6.15,
      "flags": {"streak": true, "transient_px": 312}
    }
  ],
  "streaks": []
}
```

- `wavelength_um`: wavelength at the target position (each SPHEREx pixel sees a different wavelength).
- `flags.streak`: the pipeline flagged a satellite streak in this cutout.

## Streak object (inside `streaks`)

```json
{
  "id": "s1",
  "frame_id": "2026W34_1A_0238_2D3",
  "partner_frame_id": "2026W34_1A_0238_2D6",
  "line_px": [[10, 20], [190, 170]],
  "time_half": "first",
  "temp_k": 269,
  "temp_range_k": [251, 297],
  "ratio_long_short": 84,
  "ratio_stars": 0.24,
  "satellite": null,
  "distance_km": null,
  "spectrum": [{"um": 4.47, "value": 12.7, "err": 0.6}]
}
```

- `line_px`: streak end points in the PNG's pixel coordinates (origin top-left), for drawing an overlay.
- `temp_k`, `temp_range_k`: blackbody temperature fitted to the 4.4–5.0 µm part; `null` when the signal is too weak.
- `ratio_long_short`: streak brightness at 4.4–5 µm divided by 1.7–2.4 µm; `ratio_stars`: same ratio for stars in the same image (control).
- `satellite`, `distance_km`: filled only when the streak is matched to a catalogued object, else `null`.

## URL parameters for the frontend

`?ra=<deg>&dec=<deg>&fov=<deg>` (or `?target=barnard`) so every view can be shared and embedded.

## Test targets

| Button | RA (deg) | Dec (deg) |
| --- | --- | --- |
| Barnard's Star | 269.452 | 4.693 |
| Epsilon Indi B | 331.044 | -56.783 |
| WISE 0855 | 133.7951 | -7.2452 |
| RW Aurigae | 76.956 | 30.401 |
| R Draconis | 248.168 | 66.755 |

Coordinates are approximate catalogue (J2000-era) positions. Barnard's Star, Epsilon Indi B and WISE 0855
move several arcseconds per year, so the backend applies proper motion to the observation date
before cutting images.
