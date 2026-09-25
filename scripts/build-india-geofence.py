"""Regenerate the backend's India geofence from the published ADM1 boundaries.

Why this exists
---------------
`POST /api/v1/reports` is open and unauthenticated, so a report's coordinates
are attacker-controlled. The platform is India-only, so a report outside India is
either a mistake or abuse, and both should be refused server-side rather than
snapped into an H3 cell and given a modeled plume.

A bounding box would not do: India's bbox (roughly 68.1-97.4 E, 6.7-37.1 N) also
contains most of Nepal and Bhutan and a strip of Bangladesh, so a box check
would wave through obvious out-of-country reports.

Why ADM1 (states and union territories), not the country outline
----------------------------------------------------------------
The geofence is built from `india_states.geojson` rather than the dissolved
`india_country.geojson` because **the dissolved country outline omits small
Union Territories** - it has no ring covering Diu, so a point-in-polygon test
against it refuses reports from Indian territory. "Inside any state or union
territory" is both the correct and the more complete test: ADM1 is the
administered-level dataset, it covers all 36 states and UTs including the
islands, and a point inside any of them is inside India by definition.

    source      frontend/public/data/india_states.geojson
    publisher   geoBoundaries ADM1 (https://www.geoboundaries.org)
    license     ODC-ODbL
    upstream    names normalized to ASCII

Usage
-----
    python scripts/build-india-geofence.py [--tolerance 0.02] [--check]

`--check` re-derives the asset and exits non-zero if the committed file differs,
so a change to the upstream boundaries is caught rather than silently accepted.

Simplification is plain Douglas-Peucker at a fixed tolerance, chosen so the
result is a validation aid and not a cartographic product. Re-running this
script is deterministic: same input, same output, no seed.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "frontend" / "public" / "data" / "india_states.geojson"
TARGET = REPO_ROOT / "backend" / "app" / "domain" / "data" / "india_geofence.json"

# ~2 km at India's latitude. Coarse on purpose: this decides "is this report in
# India", not where a border runs.
DEFAULT_TOLERANCE_DEGREES = 0.02
COORDINATE_PRECISION = 4


def _perpendicular_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Distance from `point` to the segment start->end, in degrees.

    Degrees are not metres, but the tolerance is in degrees too and the outline
    does not approach a pole, so a planar approximation is adequate for a
    validation aid. Keeping it planar makes this dependency-free and exactly
    reproducible.
    """
    x, y = point[0] - start[0], point[1] - start[1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator == 0.0:
        return math.hypot(x, y)
    t = max(0.0, min(1.0, (x * dx + y * dy) / denominator))
    return math.hypot(x - t * dx, y - t * dy)


def douglas_peucker(
    points: list[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative so a long ring cannot blow the recursion limit."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst_index, worst_distance = None, 0.0
        for index in range(first + 1, last):
            distance = _perpendicular_distance(points[index], points[first], points[last])
            if distance > worst_distance:
                worst_index, worst_distance = index, distance
        if worst_index is not None and worst_distance > tolerance:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))
    return [point for point, flag in zip(points, keep, strict=True) if flag]


def _polygons(geometry: dict) -> list[list[list[float]]]:
    """GeoJSON Polygon/MultiPolygon -> list of rings (each a list of [lon, lat])."""
    if geometry["type"] == "Polygon":
        return [list(ring) for ring in geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return [list(ring) for polygon in geometry["coordinates"] for ring in polygon]
    raise SystemExit(f"unsupported geometry type: {geometry['type']}")


def _simplify_ring(ring: list[list[float]], tolerance: float) -> tuple[list[list[float]], bool]:
    """Simplify one closed ring, never losing it.

    Returns `(points, simplified)`. A ring whose simplified form collapses below
    three points is kept in its original form instead of being dropped: small
    island territories (Diu is about 11 km2) are smaller than the tolerance
    itself, and dropping them would make the geofence refuse reports from
    Indian territory. Keeping such a ring costs a handful of vertices, because a
    ring small enough to collapse is already short.
    """
    closed = ring[0] == ring[-1]
    path = ring[:-1] if closed else ring
    reduced = douglas_peucker([tuple(point) for point in path], tolerance)
    if len(reduced) < 3:
        return (
            [
                [round(point[0], COORDINATE_PRECISION), round(point[1], COORDINATE_PRECISION)]
                for point in path
            ]
        ), False
    if reduced[0] != reduced[-1]:
        reduced.append(reduced[0])
    return (
        [
            [round(point[0], COORDINATE_PRECISION), round(point[1], COORDINATE_PRECISION)]
            for point in reduced
        ],
        True,
    )


def build(tolerance: float) -> dict:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    areas: list[dict] = []
    unsimplified_rings = 0
    for feature in document["features"]:
        rings: list[list[list[float]]] = []
        for ring in _polygons(feature["geometry"]):
            points, simplified = _simplify_ring(ring, tolerance)
            if not simplified:
                unsimplified_rings += 1
            rings.append(points)
        if not rings:
            raise SystemExit(
                f"area {feature['properties'].get('name')!r} produced no rings; "
                "refusing to write a geofence that is missing Indian territory"
            )
        # Biggest ring first within an area, so a reader sees the mainland part
        # of a multi-part state before its islets.
        rings.sort(key=len, reverse=True)
        areas.append({"name": feature["properties"].get("name", "unnamed"), "rings": rings})
    # Largest areas first: a point in the mainland resolves on the first few
    # areas tested, so this ordering is also the cheap path.
    areas.sort(key=lambda area: sum(len(ring) for ring in area["rings"]), reverse=True)

    return {
        "provenance": {
            "source_file": "frontend/public/data/india_states.geojson",
            "publisher": "geoBoundaries ADM1 (https://www.geoboundaries.org)",
            "license": "ODC-ODbL",
            "basis": "states and union territories (36 areas, ADM1)",
            "backend_processing": (
                f"Douglas-Peucker at {tolerance} degrees "
                f"(~{tolerance * 111:.1f} km), coordinates rounded to "
                f"{COORDINATE_PRECISION} decimals; rings smaller than the "
                f"tolerance are kept unsimplified ({unsimplified_rings} of them)"
            ),
            "regenerate_with": "python scripts/build-india-geofence.py",
            "purpose": (
                "server-side validation that a citizen fire report is inside India; "
                "NOT a cartographic or legal boundary"
            ),
            "why_not_the_country_outline": (
                "the dissolved india_country.geojson omits small union territories "
                "(no ring covers Diu), so ADM1 is used instead"
            ),
        },
        "area_count": len(areas),
        "point_count": sum(len(ring) for area in areas for ring in area["rings"]),
        "unsimplified_ring_count": unsimplified_rings,
        "areas": areas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_DEGREES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed asset differs from a fresh build.",
    )
    args = parser.parse_args()

    payload = build(args.tolerance)
    body = json.dumps(payload, indent=1) + "\n"

    if args.check:
        if not TARGET.exists():
            print(f"missing {TARGET}")
            return 1
        if TARGET.read_text(encoding="utf-8") != body:
            print(f"{TARGET} is stale; re-run scripts/build-india-geofence.py")
            return 1
        print(f"{TARGET.name} is up to date ({payload['point_count']} points)")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(body, encoding="utf-8")
    print(
        f"wrote {TARGET} - {payload['area_count']} areas, "
        f"{payload['point_count']} points, {TARGET.stat().st_size:,} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
