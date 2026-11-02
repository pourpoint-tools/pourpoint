"""Write a tiny independent hole/multipart input, without acquiring source data."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, box


def write_fixture(path: Path) -> None:
    ring = Polygon([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)], holes=[[(1, 1), (1, 3), (3, 3), (3, 1), (1, 1)]])
    values = [ring, MultiPolygon([box(4, 0, 5, 4), box(10, 0, 11, 1)])]
    with path.open("xb") as stream:
        for identifier, geometry in zip([100, 300], values, strict=True):
            value = geometry.wkb
            stream.write(struct.pack("<QI", identifier, len(value)))
            stream.write(value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    write_fixture(parser.parse_args().output)
