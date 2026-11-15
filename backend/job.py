"""Run one long job in its own process: `python -m backend.job frames KEY RA DEC SIZE PER_EPOCH [NAME]`
or `python -m backend.job streaks KEY URL DET`.

Shared hosts (LiteSpeed/Passenger) may freeze or recycle the web worker between requests, which
silently kills a background thread. A detached process keeps going regardless. It removes the
job's .run marker when done and leaves an .err file if it failed, exactly like the thread did.
"""
import os
import sys
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import frames, spherex, streaks  # noqa: E402

JOBS = spherex.CACHE / "jobs"


def main(argv):
    kind, key = argv[0], argv[1]
    run, err = JOBS / f"{key}.run", JOBS / f"{key}.err"
    try:
        if kind == "frames":
            ra, dec, size, per = float(argv[2]), float(argv[3]), float(argv[4]), int(argv[5])
            frames.build(ra, dec, size_deg=size, per_epoch_det=per, name=(argv[6] if len(argv) > 6 and argv[6] else None))
        elif kind == "streaks":
            streaks.analyse(argv[2], int(argv[3]))
        else:
            raise ValueError(f"unknown job kind {kind}")
    except Exception as e:  # noqa: BLE001  report any failure to the next poll
        err.write_text(str(e)[:300])
    finally:
        run.unlink(missing_ok=True)


if __name__ == "__main__":
    main(sys.argv[1:])
