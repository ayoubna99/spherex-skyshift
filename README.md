# SkyShift — what moved through your patch of sky?

**NASA Space Apps Challenge 2026 · Planet X and SPHEREx**

Point at any patch of sky and see what moved through it in NASA SPHEREx images: stars, asteroids,
and the satellites crossing the data — and, for the satellites, how hot they are.

> Work in progress (hackathon weekend, Nov 14–15, 2026).

## Structure

| Folder | What |
| --- | --- |
| `backend/` | Python API: finds SPHEREx images at a position, makes cutouts and PNG frames, measures satellite streaks |
| `frontend/` | Web app: search, test-target buttons, sky map (Aladin Lite), flipbook player |
| `mock/` | Fake data with the same shape as the real API, for building the frontend early |
| `docs/` | API contract and notes |

## Team

Ayoub (data, backend), Abdou (frontend), Ilyes (video and media).

## Data and credits

Built on NASA SPHEREx Quick Release data from the NASA/IPAC Infrared Science Archive (IRSA).
Full list of data sources, tools and licenses: to be completed.

## License

MIT — see `LICENSE`.
