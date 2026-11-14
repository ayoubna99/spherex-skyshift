"""The challenge's test targets, with motion so we can centre cutouts where the object really is.

Positions: J2000 coordinates at the given epoch; proper motions in arcsec/yr (RA term includes cos(dec)).
Values are approximate literature values; they only need to be good to a few arcseconds
because the cutouts are several arcminutes wide.
"""
import math

TARGETS = {
    "barnard": {
        "name": "Barnard's Star", "ra": 269.452083, "dec": 4.693389, "epoch": 2000.0,
        "pm_ra": -0.7986, "pm_dec": 10.328, "size_deg": 0.12,
        "note": "The fastest-moving star in our sky: about 10 arcseconds per year.",
    },
    "eps-indi-b": {
        "name": "Epsilon Indi B", "ra": 331.043833, "dec": -56.782694, "epoch": 2000.0,
        "pm_ra": 3.966, "pm_dec": -2.537, "size_deg": 0.12,
        "note": "A pair of brown dwarfs orbiting the star Epsilon Indi.",
    },
    "wise-0855": {
        "name": "WISE 0855-0714", "ra": 133.795129, "dec": -7.245147, "epoch": 2014.0,
        "pm_ra": -8.1, "pm_dec": 0.7, "size_deg": 0.15,
        "note": "The coldest known brown dwarf, and one of the fastest-moving.",
    },
    "rw-aur": {
        "name": "RW Aurigae", "ra": 76.956375, "dec": 30.401194, "epoch": 2000.0,
        "pm_ra": 0.0, "pm_dec": 0.0, "size_deg": 0.12,
        "note": "A young variable star whose brightness changes.",
    },
    "r-dra": {
        "name": "R Draconis", "ra": 248.1675, "dec": 66.7551, "epoch": 2000.0,
        "pm_ra": 0.0, "pm_dec": 0.0, "size_deg": 0.12,
        "note": "A pulsating red giant (Mira variable) that brightens and fades over months.",
    },
}


def position_at(key, year):
    """Target position (deg) at a decimal year, applying linear proper motion."""
    t = TARGETS[key]
    dt = year - t["epoch"]
    dec = t["dec"] + t["pm_dec"] * dt / 3600.0
    ra = t["ra"] + t["pm_ra"] * dt / 3600.0 / math.cos(math.radians(t["dec"]))
    return ra % 360.0, dec
