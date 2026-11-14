"""Shrink a Space-Track 3LE history file to one element set per object per day.

    python -m scripts.slim_tle big.txt slim.txt

Keeps the server's memory low (Render free plan). The output is still Space-Track data:
keep it private (Render secret file), never commit it.
"""
import sys


def main(src, dst):
    best = {}
    with open(src) as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    for i in range(0, len(lines) - 2):
        n, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if n.startswith("0 ") and l1.startswith("1 ") and l2.startswith("2 "):
            key = (l1[2:7], l1[18:23])           # object, epoch year+day
            best.setdefault(key, (n, l1, l2))
    with open(dst, "w") as f:
        for n, l1, l2 in best.values():
            f.write(f"{n}\n{l1}\n{l2}\n")
    print(len(best), "element sets written")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
