"""Deterministic seed/demo data — a synthetic pollution/weather field over
all of India, not a lookup table of hand-picked numbers.

Every service in app.services falls back to this ONLY when the real
repository query returns nothing — never unconditionally — so real data
takes over automatically once ingestion exists and starts writing rows.
Every value here is DEMO DATA: illustrative, fabricated, seeded from
`_SEED` — never a measurement — and every response that can include it
sets is_demo=True (see app.api.schemas) so the frontend never mistakes it
for something real. Not a "region" configuration — see docs/architecture.md's
note on that.

Design, in one pass:

  1. A smooth regional background (PM2.5, temperature, humidity, wind
     speed, precipitation, boundary-layer height) is inverse-distance-
     weighted from a dozen hand-placed `_REGION_ANCHORS` chosen to loosely
     match India's real climate/pollution geography (Indo-Gangetic plain
     hazier than the Western Ghats, Thar desert hot/dry/windy, Himalayan
     foothills cool, Northeast wet and clean, etc.) — the same IDW idea
     app.services.estimation.IDWPollutionEstimator uses for real readings,
     reused here purely for a thematically-consistent synthetic shape.
  2. `_CITIES` (the old 19-city list) each add a Gaussian-decaying PM2.5
     "pressure" bump on top of that background, sized so the field is
     approximately that city's peak value at its exact center and fades
     over ~2-3x `_CITY_BUMP_SIGMA_KM` — cities read as distinct hotspots
     rather than a flat regional plateau. A handful are flagged
     `industrial=True` and additionally bump PDI, standing in for
     industrial-belt pressure a real PDI model would pick up from
     emissions-adjacent signals we don't have here.
  3. `_ANOMALY_HOTSPOTS` are 2 more Gaussian bumps *not* tied to any city
     — synthetic stand-ins for episodic events (crop-residue burning,
     etc.) a real system would detect from satellite fire data, included
     so the map has hotspots beyond "wherever a big city already is."
  4. A small deterministic per-point jitter (seeded by a plain string key
     — see `_rng`) adds texture so the field doesn't look like a bare
     analytic function, without swamping the regional/hotspot shape.
  5. Wind direction follows a smooth latitude-based gradient (winter
     northwesterlies in the north easing toward the coast) rather than
     one nationwide constant. Forecasts (`generate_forecast`) don't
     resample this static field — instead, every hotspot from (2) and (3)
     is itself advected: `_advect` moves each one's *center* downwind by
     wind_speed*3.6*hours km (using the wind at that hotspot's own
     location — Coordinate.destination_point again, but now carrying the
     source, not sampling around a fixed one), grows its Gaussian sigma
     over time (spreading/dilution) and shrinks its peak amplitude to
     match (mass-conserving: amplitude ∝ 1/sigma², plus a small extra
     exponential removal term for deposition-like loss) — see `_advect`'s
     own docstring for the exact factors. The regional background never
     moves (it's climate, not a plume); only hotspots do. This is what
     makes stepping through Now → +1h → +3h → +6h visibly show each
     plume drifting downwind and fading, rather than every cell's number
     just quietly changing in place.
  6. Confidence is higher near a city (a real deployment would have denser
     sensors there) and decays with both distance-from-nearest-city and
     forecast horizon.
  7. Nothing is generated outside `_is_within_domain`'s loose India
     extent — see is_within_demo_domain — so a query far outside India
     (e.g. Sydney) correctly finds no demo data, same as no real data.

Determinism: `_rng` seeds `random.Random` with a plain string (never a
tuple) — CPython hashes str/bytes/int seeds via a fixed algorithm, not the
process-randomized `hash()` builtin, so the same seed string reproduces
the same sequence across processes and Python invocations. All of the
above is otherwise pure/deterministic in cell id alone (no wall-clock
dependency) and is cached per-cell (`_field_for_cell`) for speed;
`_now()` is only stamped onto the returned domain objects at the end.

PDI here (_pdi_at) is a simplified, self-contained echo of the same idea
as app.services.pdi.HeuristicPDIModel — the same weighted-average-of-
normalized-factors formula, reading the same four app.core.config.Settings
weights — but not a call into that model. Wiring the real model in would
mean threading its configuration through GridService/CellService/
WeatherService just for the demo path, which isn't worth it for
illustrative numbers; this stays honest about that rather than claiming
more rigor than it has. Reading those four settings (via _pdi_weights) is
the one deliberate exception to "this module doesn't read live settings"
above — a heuristic index whose whole point is being tunable without a
code change shouldn't have two separate, easily-out-of-sync places to
tune it.

Two kinds of function live here:
  - Continuous, cell-list-scoped: `is_within_demo_domain`,
    `generate_grid_state`, `generate_weather_reading`, `generate_forecast`
    and their `*_for_cells` batch forms — evaluate the field at exactly
    the cells a caller (GridService/WeatherService/CellService, via
    app.services.grid_query.resolve_cells) says it needs. This is what
    gives every level-of-detail tier (see frontend/src/lib/lod.ts) real
    geographic coverage — rural cells included, not just named cities.
  - Legacy discrete: `demo_cells`, `demo_sensor_readings`,
    `demo_grid_states`, `demo_weather_readings`, `demo_forecasts`,
    `demo_alerts` — used for the unfiltered "no bbox given" fallback
    (see app.services.grid's module docstring: resolution alone, without
    a bbox, has no effect) where enumerating literally every cell in
    India isn't an option. These evaluate the exact same continuous field
    at each of the 19 cities' clusters, so the sparse "zoomed all the way
    out, no viewport yet" view and the LOD-scoped views agree with each
    other at every point they share.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from app.core.config import get_settings
from app.domain.h3_grid import cell_center, cell_for, grid_disk
from app.domain.numeric import clamp
from app.domain.types import (
    PM25,
    Alert,
    AlertSeverity,
    Coordinate,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)

_SEED = "gdg-c4c-india-demo-v1"


def _rng(*parts: str) -> random.Random:
    """A random.Random seeded by a plain '|'-joined string — never a
    tuple. CPython hashes tuples of non-str/bytes/int elements via the
    process-randomized `hash()` builtin, which would make this
    non-reproducible across processes; joining to one string sidesteps
    that entirely (str seeds go through a fixed internal algorithm).
    """
    return random.Random("|".join((_SEED, *parts)))


def _now() -> datetime:
    return datetime.now(UTC)


# --- India domain: no data is generated outside this extent ---

# Deliberately a little more generous than the India bbox used elsewhere
# (tests, frontend initial view) so that every cell those callers ask for
# is safely inside — this is a "where the field is defined" boundary, not
# itself the definition of "India" for any other purpose.
_DOMAIN_MIN_LAT = 5.5
_DOMAIN_MAX_LAT = 38.5
_DOMAIN_MIN_LON = 66.5
_DOMAIN_MAX_LON = 98.5


def _is_within_domain(latitude: float, longitude: float) -> bool:
    return (
        _DOMAIN_MIN_LAT <= latitude <= _DOMAIN_MAX_LAT
        and _DOMAIN_MIN_LON <= longitude <= _DOMAIN_MAX_LON
    )


def is_within_demo_domain(h3_cell: str) -> bool:
    """True if h3_cell's center falls within the extent this module
    generates data for. Callers (CellService, the *_for_cells batch
    functions below) use this to correctly return "no data" for a cell
    far outside India, rather than fabricating a value for it.
    """
    lat, lon = cell_center(h3_cell)
    return _is_within_domain(lat, lon)


# --- regional background: inverse-distance-weighted from these anchors ---

# (latitude, longitude, pm25 ug/m3, temperature C, humidity %,
#  wind_speed m/s, precipitation mm, boundary_layer_height m,
#  greenness [0, 1]) — loosely matched to each region's real climate/
# pollution/vegetation-cover reputation so the background reads as
# plausible, not arbitrary. Illustrative, not measured. `greenness` feeds
# only the PDI "vegetation_sink" factor (see _pdi_at) — it's a separate,
# independently-authored value per region, not a transform of that same
# region's pm25 figure, so PDI's vegetation factor doesn't just become a
# second copy of the pollution level under another name.
_REGION_ANCHORS: list[tuple[float, float, float, float, float, float, float, float, float]] = [
    (28.7, 77.1, 120.0, 24.0, 55.0, 2.5, 0.0, 500.0, 0.25),  # Indo-Gangetic Plain (Delhi-NCR)
    (26.8, 82.5, 115.0, 25.0, 60.0, 2.0, 0.5, 480.0, 0.30),  # Indo-Gangetic Plain (UP/Bihar)
    (26.5, 71.5, 55.0, 31.0, 25.0, 4.0, 0.0, 900.0, 0.10),  # Thar Desert (Rajasthan)
    (32.2, 76.3, 25.0, 12.0, 55.0, 1.5, 2.0, 400.0, 0.75),  # Western Himalaya foothills
    (27.5, 93.5, 18.0, 20.0, 82.0, 1.5, 6.0, 350.0, 0.85),  # Eastern Himalaya / NE hills
    (21.0, 79.5, 55.0, 27.0, 48.0, 2.5, 1.0, 700.0, 0.45),  # Deccan Plateau / Central India
    (15.8, 74.2, 30.0, 26.0, 78.0, 3.5, 4.0, 600.0, 0.70),  # Konkan / West coast
    (11.5, 76.8, 25.0, 24.0, 75.0, 3.0, 5.0, 550.0, 0.80),  # Western Ghats / interior south
    (13.5, 79.5, 32.0, 28.0, 68.0, 3.5, 1.5, 650.0, 0.40),  # Eastern Deccan / Tamil Nadu-AP
    (20.5, 86.5, 45.0, 27.0, 72.0, 3.0, 2.0, 600.0, 0.55),  # Odisha coast
    (23.5, 88.3, 65.0, 26.0, 74.0, 2.5, 2.0, 550.0, 0.50),  # Lower Gangetic / Bengal delta
    (25.5, 92.5, 20.0, 22.0, 85.0, 1.5, 8.0, 400.0, 0.75),  # Brahmaputra Valley / NE plains
]
_ANCHOR_PM25 = 2
_ANCHOR_TEMPERATURE = 3
_ANCHOR_HUMIDITY = 4
_ANCHOR_WIND_SPEED = 5
_ANCHOR_PRECIPITATION = 6
_ANCHOR_BLH = 7
_ANCHOR_GREENNESS = 8

# Softens the IDW weight so it never blows up exactly at an anchor
# (1/distance**2 -> inf at distance 0); ~5km worth of softening.
_IDW_SOFTENING_KM2 = 25.0


def _idw_background(
    latitude: float, longitude: float
) -> tuple[float, float, float, float, float, float, float]:
    """(pm25, temperature, humidity, wind_speed, precipitation,
    boundary_layer_height, greenness) inverse-distance-weighted from
    _REGION_ANCHORS.
    """
    weight_total = 0.0
    sums = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    for anchor in _REGION_ANCHORS:
        distance = _haversine_km(latitude, longitude, anchor[0], anchor[1])
        weight = 1.0 / (distance * distance + _IDW_SOFTENING_KM2)
        weight_total += weight
        for i in range(7):
            sums[i] += weight * anchor[i + 2]
    return tuple(s / weight_total for s in sums)  # type: ignore[return-value]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Same formula as Coordinate.distance_km, inlined on raw floats: this
    runs inside per-cell hot loops (an anchor/city/hotspot pass per cell,
    over up to tens of thousands of cells for a state-tier read), and
    skipping the Coordinate object per comparison is a meaningful speedup
    there.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


# --- city hotspots (also the legacy discrete demo-city list) ---

# (latitude, longitude, name, peak pm2.5 ug/m3, industrial) — the same 19
# cities as the original hand-picked dataset, now used as Gaussian-bump
# anchors on top of the smooth regional background rather than the only
# places with any data at all. `industrial=True` additionally bumps PDI,
# standing in for industrial-belt pressure. Values span good through
# hazardous so alerts have something to fire on.
_CITIES: list[tuple[float, float, str, float, bool]] = [
    (28.6139, 77.2090, "Delhi", 185.0, False),
    (26.4499, 80.3319, "Kanpur", 165.0, True),
    (26.8467, 80.9462, "Lucknow", 150.0, False),
    (25.5941, 85.1376, "Patna", 145.0, False),
    (30.7333, 76.7794, "Chandigarh", 80.0, False),
    (26.9124, 75.7873, "Jaipur", 110.0, False),
    (23.2599, 77.4126, "Bhopal", 70.0, False),
    (21.1458, 79.0882, "Nagpur", 60.0, True),
    (22.5726, 88.3639, "Kolkata", 95.0, False),
    (26.1445, 91.7362, "Guwahati", 75.0, False),
    (23.0225, 72.5714, "Ahmedabad", 90.0, True),
    (18.5204, 73.8567, "Pune", 58.0, False),
    (19.0760, 72.8777, "Mumbai", 65.0, False),
    (17.3850, 78.4867, "Hyderabad", 55.0, False),
    (17.6868, 83.2185, "Visakhapatnam", 40.0, True),
    (13.0827, 80.2707, "Chennai", 38.0, False),
    (12.9716, 77.5946, "Bengaluru", 35.0, False),
    (9.9312, 76.2673, "Kochi", 22.0, False),
    (8.5241, 76.9366, "Thiruvananthapuram", 20.0, False),
]

# 2 synthetic hotspots not tied to any city — stand-ins for episodic
# events (crop-residue burning season, a localized industrial pocket) a
# real system would learn from satellite fire data or emissions
# inventories rather than an interpolated field. (latitude, longitude,
# label, pm2.5 bump at center, sigma_km).
_ANOMALY_HOTSPOTS: list[tuple[float, float, str, float, float]] = [
    (30.3, 75.5, "Punjab crop-residue burning plume (synthetic)", 130.0, 45.0),
    (26.2, 85.4, "North Bihar haze pocket (synthetic)", 60.0, 30.0),
]

_CITY_BUMP_SIGMA_KM = 35.0
_INDUSTRIAL_PDI_BUMP = 20.0

# How a hotspot's plume evolves per hour of forecast horizon — the whole
# "pollution movement" model, in two numbers:
#   - _HOTSPOT_SPREAD_RATE: fractional growth of the Gaussian's sigma per
#     hour (dispersion — the plume covers a wider area as it ages).
#   - _HOTSPOT_REMOVAL_RATE: extra fractional loss per hour on top of
#     that spreading (deposition/chemical loss — real pollutants don't
#     just spread, some of the mass actually leaves the system). Applied
#     as exp(-rate*hours), the standard form for a constant removal rate.
# Both apply only to hotspots — see _advect — never to the regional
# background, which represents ambient climate, not a moving plume.
_HOTSPOT_SPREAD_RATE = 0.2
_HOTSPOT_REMOVAL_RATE = 0.05


@lru_cache(maxsize=len(_CITIES))
def _background_at_city(index: int) -> float:
    """The smooth regional PM2.5 background at city `index`'s own
    coordinates — used so that city's bump amplitude is sized to land
    approximately on its peak value at the exact center, not
    peak-plus-background (which would run hot). Cached: called once per
    city per process, not per queried cell.
    """
    lat, lon, _name, _peak, _industrial = _CITIES[index]
    return _idw_background(lat, lon)[0]


@dataclass(frozen=True)
class _AdvectedHotspot:
    """One hotspot's shape at a specific forecast horizon: where its
    Gaussian is now centered, how wide it's spread, and what's left of
    its peak — see _advect.
    """

    latitude: float
    longitude: float
    amplitude: float
    industrial_bump: float
    sigma_km: float


def _advect(
    latitude: float,
    longitude: float,
    amplitude0: float,
    sigma0_km: float,
    industrial_bump0: float,
    hours: float,
) -> _AdvectedHotspot:
    """Where this one hotspot's plume is, and what it looks like, `hours`
    after "now" — the model behind "the plume visibly moves" rather than
    every cell's number just changing in place:

      1. Advection: the plume's center moves downwind by
         wind_speed*3.6*hours km (m/s -> km/h * hours), using the wind at
         the hotspot's OWN (unmoved) location — the same real-world unit
         conversion generate_forecast used before this, just now moving
         the source instead of resampling around a fixed one.
         wind_direction is meteorological convention (the direction wind
         blows FROM), so downwind is the opposite bearing.
      2. Dispersion: sigma grows linearly with hours (_HOTSPOT_SPREAD_RATE)
         — the same total "stuff" now covers a wider area.
      3. Dilution + removal: amplitude shrinks by (sigma0/sigma)**2 to
         match #2 (a 2D Gaussian's peak scales as 1/sigma² to conserve
         its integral — spread the same mass over a wider area and the
         peak drops accordingly) times an extra exp(-removal_rate*hours)
         for deposition/chemical loss. Amplitude only ever shrinks here —
         never grows — so a downwind cell's forecast rising means this
         plume has now reached it, not pollution appearing from nowhere.

    hours=0 (the "current" field) returns the hotspot completely
    unmoved/unshrunk, so this is a strict generalization of the old
    static bump, not a separate code path with its own edge cases.
    """
    if hours <= 0:
        return _AdvectedHotspot(latitude, longitude, amplitude0, industrial_bump0, sigma0_km)

    _, _, _, wind_speed, _, _, _ = _idw_background(latitude, longitude)
    downwind_bearing = (_wind_direction(latitude) + 180.0) % 360.0
    distance_km = wind_speed * 3.6 * hours
    moved = Coordinate(latitude, longitude).destination_point(downwind_bearing, distance_km)

    sigma_km = sigma0_km * (1 + _HOTSPOT_SPREAD_RATE * hours)
    decay_factor = (sigma0_km / sigma_km) ** 2 * math.exp(-_HOTSPOT_REMOVAL_RATE * hours)
    return _AdvectedHotspot(
        moved.latitude,
        moved.longitude,
        amplitude0 * decay_factor,
        industrial_bump0 * decay_factor,
        sigma_km,
    )


@lru_cache(maxsize=100)
def _advected_city(index: int, hours: float) -> _AdvectedHotspot:
    """Cached per (city, hours) — not per queried cell: only a handful of
    distinct `hours` values are ever requested (0 for "current", 1/3/6
    for a forecast horizon), so this is computed at most 4x per city per
    process no matter how many thousands of cells a state-tier read asks
    for, and every one of those cells' _hotspot_bumps_at calls reuses it.
    """
    lat, lon, _name, peak, industrial = _CITIES[index]
    amplitude0 = max(0.0, peak - _background_at_city(index))
    industrial_bump0 = _INDUSTRIAL_PDI_BUMP if industrial else 0.0
    return _advect(lat, lon, amplitude0, _CITY_BUMP_SIGMA_KM, industrial_bump0, hours)


@lru_cache(maxsize=100)
def _advected_anomaly(index: int, hours: float) -> _AdvectedHotspot:
    lat, lon, _label, peak_bump, sigma_km = _ANOMALY_HOTSPOTS[index]
    return _advect(lat, lon, peak_bump, sigma_km, 0.0, hours)


def _hotspot_bumps_at(latitude: float, longitude: float, hours: float) -> tuple[float, float, float]:
    """(pm25_bump, industrial_pdi_bump, nearest_city_km) at `hours` from
    now — hours=0 (used by the "current" field) matches the original
    static-hotspot shape exactly; hours>0 (used by generate_forecast)
    evaluates every hotspot's _advect-ed position/spread/amplitude
    instead. nearest_city_km always uses cities' real, unmoved locations
    — confidence is about distance to wherever a real deployment would
    have denser instrumentation, which doesn't move because a plume did.
    """
    pm25_bump = 0.0
    industrial_bump = 0.0
    nearest_city_km = math.inf
    for index, (lat, lon, _name, _peak, _industrial) in enumerate(_CITIES):
        distance_to_city = _haversine_km(latitude, longitude, lat, lon)
        nearest_city_km = min(nearest_city_km, distance_to_city)
        hotspot = _advected_city(index, hours)
        # At hours=0 (the "current" field — by far the hottest path: every
        # grid/current and weather request, versus forecasts only when a
        # horizon is selected) _advect returns the hotspot exactly where
        # the city already is, so this is the same distance just computed
        # above — recomputing it would be a second haversine call (real
        # trig, not free) for a value we already have.
        distance = (
            distance_to_city
            if hours == 0
            else _haversine_km(latitude, longitude, hotspot.latitude, hotspot.longitude)
        )
        if distance > 5 * hotspot.sigma_km:
            continue
        decay = math.exp(-(distance * distance) / (2 * hotspot.sigma_km**2))
        pm25_bump += hotspot.amplitude * decay
        industrial_bump += hotspot.industrial_bump * decay
    for index in range(len(_ANOMALY_HOTSPOTS)):
        hotspot = _advected_anomaly(index, hours)
        distance = _haversine_km(latitude, longitude, hotspot.latitude, hotspot.longitude)
        if distance > 5 * hotspot.sigma_km:
            continue
        decay = math.exp(-(distance * distance) / (2 * hotspot.sigma_km**2))
        pm25_bump += hotspot.amplitude * decay
    return pm25_bump, industrial_bump, nearest_city_km


# --- wind direction: a smooth geographic gradient, not one constant ---

# A wintertime-northwesterly-ish pattern that eases as latitude drops
# toward the coast — a believable "varies geographically" shape without
# claiming to model real synoptic wind. Per-cell jitter is layered on
# top (see _field_at) so neighbors don't share an identical value.
_WIND_DIRECTION_BASE_DEG = 280.0
_WIND_DIRECTION_LAT_SLOPE = 0.6  # degrees per degree of latitude above 15N


def _wind_direction(latitude: float) -> float:
    return (_WIND_DIRECTION_BASE_DEG - _WIND_DIRECTION_LAT_SLOPE * (latitude - 15.0)) % 360.0


# --- the field itself ---


# Road/activity pressure decays with distance from the nearest city,
# tighter than the pollution bump's own sigma (_CITY_BUMP_SIGMA_KM) —
# "road density" falls off closer to a city's edge than ambient air
# pollution, which drifts further with wind. Present near EVERY city,
# unlike industrial_pressure (only cities flagged industrial=True):
# ordinary traffic/activity exists wherever a city does.
_ROAD_PRESSURE_SIGMA_KM = 25.0


@lru_cache(maxsize=1)
def _pdi_weights() -> tuple[dict[str, float], float]:
    """(factor weights, pm25_reference) — sourced from the same
    app.core.config.Settings the real HeuristicPDIModel reads. The one
    deliberate exception to this module's "no live settings" rule (see
    module docstring): a heuristic index whose entire point is being
    tunable without a code change shouldn't have two separate,
    easily-out-of-sync places to tune it.

    Cached (Settings itself doesn't change at runtime — get_settings()
    is already @lru_cache'd for the same reason): this is called once
    per cell inside _pdi_at, and a state-tier read can mean tens of
    thousands of cells, so re-reading four attributes and rebuilding a
    dict that would come out identical every time is pure waste.
    """
    settings = get_settings()
    weights = {
        "pm25": settings.pdi_pm25_weight,
        "industrial_pressure": settings.pdi_industrial_pressure_weight,
        "road_pressure": settings.pdi_road_pressure_weight,
        "vegetation_sink": settings.pdi_vegetation_sink_weight,
    }
    return weights, settings.pdi_pm25_reference_ugm3


def _pdi_at(
    pm25: float, industrial_bump: float, nearest_city_km: float, greenness: float
) -> tuple[float, dict[str, float]]:
    """(pdi, factors) for one point — a simplified, self-contained echo
    of HeuristicPDIModel's formula (same weighted-average-of-normalized-
    factors idea, same Settings-sourced weights), not a call into that
    model — see the module docstring for why.

    Four factors, always all present here (unlike the real model, which
    can have fewer when a real data source is missing): "pm25" (this
    cell's own pollution level), "industrial_pressure" (its share of
    _hotspot_bumps_at's industrial bump), "road_pressure" (Gaussian
    falloff from the nearest city — present near any city, not just an
    industrial one), and "vegetation_sink" (regional greenness — see
    _REGION_ANCHORS — pulling the index DOWN via a negative default
    weight, not up). The last three are geographic/infrastructure
    signals independent of what pm25 happens to be at this exact point,
    which is what keeps PDI from just tracking PM2.5 one-for-one — two
    cells with the same pollution level can still land on different PDI
    scores.
    """
    weights, pm25_reference = _pdi_weights()
    factors = {
        "pm25": clamp(pm25 / pm25_reference, 0.0, 1.0),
        "industrial_pressure": clamp(industrial_bump / _INDUSTRIAL_PDI_BUMP, 0.0, 1.0),
        "road_pressure": clamp(
            math.exp(-(nearest_city_km * nearest_city_km) / (2 * _ROAD_PRESSURE_SIGMA_KM**2)),
            0.0,
            1.0,
        ),
        "vegetation_sink": clamp(greenness, 0.0, 1.0),
    }
    weight_total = sum(abs(w) for w in weights.values())
    if weight_total == 0:
        return 0.0, factors
    weighted_sum = sum(factors[name] * weights[name] for name in factors)
    pdi = clamp(100.0 * weighted_sum / weight_total, -100.0, 100.0)
    return pdi, factors


@dataclass(frozen=True)
class _Field:
    pm25: float
    wind_speed: float
    wind_direction: float
    precipitation: float
    temperature: float
    humidity: float
    boundary_layer_height: float
    pdi: float
    pdi_factors: dict[str, float]
    confidence: float


def _field_at(latitude: float, longitude: float, seed_key: str) -> _Field:
    background_pm25, temperature, humidity, wind_speed, precipitation, blh, greenness = (
        _idw_background(latitude, longitude)
    )
    pm25_bump, industrial_bump, nearest_city_km = _hotspot_bumps_at(latitude, longitude, hours=0)
    pm25 = background_pm25 + pm25_bump
    wind_direction = _wind_direction(latitude)

    rng = _rng(seed_key)
    pm25 *= 1 + rng.uniform(-0.06, 0.06)
    temperature += rng.uniform(-1.5, 1.5)
    humidity += rng.uniform(-5.0, 5.0)
    wind_speed += rng.uniform(-0.5, 0.5)
    precipitation += rng.uniform(-0.3, 0.3)
    blh += rng.uniform(-50.0, 50.0)
    wind_direction += rng.uniform(-20.0, 20.0)

    pm25 = clamp(pm25, 8.0, 500.0)
    temperature = clamp(temperature, -5.0, 46.0)
    humidity = clamp(humidity, 10.0, 100.0)
    wind_speed = clamp(wind_speed, 0.2, 20.0)
    precipitation = max(0.0, precipitation)
    blh = clamp(blh, 100.0, 3000.0)
    wind_direction %= 360.0

    pdi, pdi_factors = _pdi_at(pm25, industrial_bump, nearest_city_km, greenness)

    confidence = clamp(0.55 - 0.0015 * nearest_city_km, 0.05, 0.55)

    return _Field(
        pm25=round(pm25, 1),
        wind_speed=round(wind_speed, 2),
        wind_direction=round(wind_direction, 1),
        precipitation=round(precipitation, 2),
        temperature=round(temperature, 1),
        humidity=round(humidity, 1),
        boundary_layer_height=round(blh, 0),
        pdi=round(pdi, 1),
        pdi_factors={name: round(value, 3) for name, value in pdi_factors.items()},
        confidence=round(confidence, 2),
    )


@lru_cache(maxsize=200_000)
def _field_for_cell(h3_cell: str) -> _Field:
    lat, lon = cell_center(h3_cell)
    return _field_at(lat, lon, h3_cell)


# --- public, cell-list-scoped API (LOD-aware: covers exactly what's asked) ---


def generate_grid_state(h3_cell: str, *, timestamp: datetime | None = None) -> GridState:
    field = _field_for_cell(h3_cell)
    return GridState(
        h3_cell=h3_cell,
        timestamp=timestamp or _now(),
        pm25=field.pm25,
        pdi=field.pdi,
        confidence=field.confidence,
        wind_speed=field.wind_speed,
        wind_direction=field.wind_direction,
    )


def generate_pdi_factors(h3_cell: str) -> dict[str, float]:
    """The normalized [0, 1] value of each factor behind this cell's PDI
    (see _pdi_at) — "pm25", "industrial_pressure", "road_pressure",
    "vegetation_sink" — for CellService's "what's behind this score"
    detail view. Reuses _field_for_cell's cache: this is not a second
    computation, just reading the same _Field generate_grid_state already
    built for this cell.
    """
    return dict(_field_for_cell(h3_cell).pdi_factors)


def generate_weather_reading(h3_cell: str, *, timestamp: datetime | None = None) -> WeatherReading:
    lat, lon = cell_center(h3_cell)
    field = _field_for_cell(h3_cell)
    return WeatherReading(
        h3_cell=h3_cell,
        latitude=lat,
        longitude=lon,
        wind_speed=field.wind_speed,
        wind_direction=field.wind_direction,
        precipitation=field.precipitation,
        boundary_layer_height=field.boundary_layer_height,
        temperature=field.temperature,
        humidity=field.humidity,
        measured_at=timestamp or _now(),
    )


@lru_cache(maxsize=200_000)
def _forecast_core(h3_cell: str, hours: float) -> tuple[float, float]:
    """(predicted_pm25, confidence) — everything about a forecast except
    its timestamps, which is a pure function of (h3_cell, hours) and
    therefore cacheable exactly like _field_for_cell.

    Unlike the "current" field, this does NOT resample a static field
    around the cell — it evaluates the same regional background (which
    doesn't move) plus every hotspot's own _advect-ed shape at `hours`
    (which does — see _advect and the module docstring). A cell's
    forecast rises when an advected plume has moved close enough to
    reach it, and falls as that plume's peak keeps shrinking with
    distance and time — exactly the "plume moves downwind and disperses"
    behavior stepping through the Now/+1h/+3h/+6h horizons should show.
    """
    lat, lon = cell_center(h3_cell)
    background_pm25 = _idw_background(lat, lon)[0]
    pm25_bump, _industrial_bump, _nearest_city_km = _hotspot_bumps_at(lat, lon, hours)
    pm25 = background_pm25 + pm25_bump

    jitter = _rng(h3_cell, "forecast", str(hours)).uniform(-0.03, 0.03)
    predicted_pm25 = clamp(pm25 * (1 + jitter), 5.0, 500.0)
    confidence = clamp(_field_for_cell(h3_cell).confidence - 0.035 * hours, 0.05, 0.55)
    return round(predicted_pm25, 1), round(confidence, 2)


def generate_forecast(h3_cell: str, hours: float, *, timestamp: datetime | None = None) -> Forecast:
    now = timestamp or _now()
    predicted_pm25, confidence = _forecast_core(h3_cell, hours)
    return Forecast(
        h3_cell=h3_cell,
        generated_at=now,
        forecast_time=now + timedelta(hours=hours),
        forecast_hours=hours,
        predicted_pm25=predicted_pm25,
        confidence=confidence,
    )


def grid_states_for_cells(cells: list[str]) -> list[GridState]:
    now = _now()
    return [generate_grid_state(c, timestamp=now) for c in cells if is_within_demo_domain(c)]


def weather_readings_for_cells(cells: list[str]) -> list[WeatherReading]:
    now = _now()
    return [generate_weather_reading(c, timestamp=now) for c in cells if is_within_demo_domain(c)]


def forecasts_for_cells(cells: list[str], hours: float) -> list[Forecast]:
    now = _now()
    return [generate_forecast(c, hours, timestamp=now) for c in cells if is_within_demo_domain(c)]


# --- legacy discrete API: the unfiltered ("no bbox given") fallback ---

# Below this resolution (country/state tiers — see frontend/src/lib/lod.ts),
# a single H3 cell is already tens to hundreds of km wide, so a 1-ring
# cluster around each city would overlap neighboring cities; only fine
# (city/local-tier) resolutions get the multi-hex "blob" treatment.
_CLUSTER_MIN_RESOLUTION = 7


def _city_cluster_cells(resolution: int) -> list[list[str]]:
    """Each city's cluster of cells at `resolution`: just its center cell
    below _CLUSTER_MIN_RESOLUTION, or the center plus its immediate
    grid_disk(k=1) ring at finer resolutions — index-aligned with
    _CITIES. Values for every cell in a cluster come from the same
    continuous field as everywhere else (see generate_grid_state etc.),
    so they already fade out from the center on their own; no separate
    falloff constant is needed here.
    """
    ring_k = 1 if resolution >= _CLUSTER_MIN_RESOLUTION else 0
    return [
        grid_disk(cell_for(lat, lon, resolution=resolution), ring_k)
        for lat, lon, _name, _peak, _industrial in _CITIES
    ]


def demo_cells(resolution: int) -> list[str]:
    return [cell for cluster in _city_cluster_cells(resolution) for cell in cluster]


def demo_sensor_readings() -> list[SensorReading]:
    # One reading per CITY (a SensorReading models a physical station),
    # at the city's exact coordinates rather than a cell center — stays
    # 1:1 with _CITIES even though demo_cells() above expands each city
    # into a small interpolated-looking cluster; that's the same
    # station-vs-grid distinction the real pipeline draws.
    now = _now()
    readings = []
    for i, (lat, lon, _name, _peak, _industrial) in enumerate(_CITIES):
        field = _field_at(lat, lon, f"station-{i}")
        readings.append(
            SensorReading(
                source="demo",
                external_sensor_id=f"demo-{i}",
                latitude=lat,
                longitude=lon,
                pollutant=PM25,
                value=field.pm25,
                unit="ug/m3",
                measured_at=now - timedelta(minutes=5 * i),
            )
        )
    return readings


def demo_weather_readings(resolution: int) -> list[WeatherReading]:
    return weather_readings_for_cells(demo_cells(resolution))


def demo_grid_states(resolution: int) -> list[GridState]:
    return grid_states_for_cells(demo_cells(resolution))


def demo_forecasts(resolution: int, hours: float) -> list[Forecast]:
    return forecasts_for_cells(demo_cells(resolution), hours)


# Mirrors ALERT_CRITICAL_THRESHOLD_UGM3's default (121.0, the CPCB NAQI
# PM2.5 "Very Poor" band boundary): this module doesn't read live settings
# (see module docstring - deterministic, config-independent placeholders),
# so the demo alert's severity is pinned to the same default a real
# pipeline run would use, not derived from it.
_CRITICAL_PM25_THRESHOLD = 121.0


def demo_alerts(resolution: int) -> list[Alert]:
    now = _now()
    # Whichever city's generated PM2.5 is currently highest — computed
    # rather than hardcoded so this can't silently drift if _CITIES is
    # ever reordered or extended. Anchored to the city's own center cell,
    # where that peak value actually applies.
    city_pm25 = [
        generate_grid_state(cell_for(lat, lon, resolution=resolution)).pm25
        for lat, lon, _name, _peak, _industrial in _CITIES
    ]
    worst_index = max(range(len(city_pm25)), key=lambda i: city_pm25[i])
    worst_lat, worst_lon, _name, _peak, _industrial = _CITIES[worst_index]
    alert_cell = cell_for(worst_lat, worst_lon, resolution=resolution)
    current_pm25 = city_pm25[worst_index]
    is_critical = current_pm25 >= _CRITICAL_PM25_THRESHOLD
    severity = AlertSeverity.CRITICAL if is_critical else AlertSeverity.WARNING
    return [
        Alert(
            h3_cell=alert_cell,
            severity=severity,
            message=f"Demo alert: PM2.5 is {current_pm25:.0f} µg/m³ now — {severity.value} level.",
            created_at=now,
            current_pm25=current_pm25,
            forecast_pm25=round(current_pm25 * 1.1, 1),
            forecast_hours=3,
            confidence=0.3,  # deliberately low: signals "placeholder", not measured
            forecast_time=now + timedelta(hours=3),
        )
    ]
