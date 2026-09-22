# Environmental source inventory

Milestone 0 contract for the environmental model. This is an implementation
decision record, not a claim that every adapter is already live. A source can
enter training or production inference only after its availability time,
units, spatial support, attribution, and retention terms are recorded in a
`DatasetRef`.

| Signal | Initial source | Role | Cadence / spatial treatment | Contract decision |
|---|---|---|---|---|
| PM2.5 observations | OpenAQ, including CPCB station records where available | Supervised target and current station evidence | Hourly or provider cadence; station point data interpolated to native H3 | Observed target; retain station identity and measured time |
| Wind, rain, temperature, humidity, mixing height | Open-Meteo forecast/current and historical forecast archives | Features and forecast inputs | Weather sampled on the configured coarse H3 grid and fanned out | Issue time and valid time are mandatory for forecast evaluation |
| Residential population | WorldPop country raster | Exposure denominator and contextual feature | Versioned raster aggregated to native H3 | Population never directly added to PM2.5 |
| Roads and land-use activity | OpenStreetMap regional extract | Static activity proxies | Road length by class and major-road distance per H3 cell | Road geometry is a proxy, not traffic volume |
| Land cover | ESA WorldCover | Built-up, vegetation and bare-soil features | Versioned raster fractions per H3 cell | Store product year/version and covered fraction |
| Satellite fires | NASA FIRMS | Upwind fire feature and map evidence | Near-real-time detections; aggregate age, FRP and confidence | Keep separate from citizen reports; do not double count |
| Traffic | Future approved traffic provider | Time-varying road activity proxy | Selected corridors first; store speed/free-flow/confidence | Missing traffic is unknown, never zero |
| Optional regional background | CAMS via a compatible provider | Coarse modeled prior/fallback | Native model resolution must be exposed | Never present as fine-scale ground truth |

## Provenance and mode

Every input bundle uses one of these kinds:

- `observed`: a measurement from a station or detection product.
- `modeled`: weather or atmospheric model output.
- `synthetic`: deterministic dummy data.
- `derived`: features calculated from other inputs.

Every response run has a mode: `live`, `demo`, or `mixed`. A mixed run is
allowed only during migration and must identify the contributing dataset
references. `is_demo` remains a compatibility flag; v2 adds the mode, run ID,
coverage and dataset attribution.

## As-of rules

`observed_at`/`valid_at` describe when a value is about. `issued_at` describes
when a forecast was issued. `available_at` describes when this application
could have used it. `ingested_at` describes when Air Health retrieved it.

Training features may use only values available by the prediction issue time.
Archived measurements must not be treated as historically available merely
because they were downloaded later. Weather backtests use issue-specific
archived forecasts where available. All stored timestamps are UTC.

## Initial source boundaries

The first trained model is regional and PM2.5-only. OpenAQ station evidence is
the target. Open-Meteo weather, calendar fields, static population/road/land
cover fields, and synthetic fire/traffic interfaces form the first feature
schema. Live FIRMS and traffic ingestion are subsequent milestones. No source
is fetched from a browser or from an API request; collection is a backend
pipeline concern.

The following attribution strings are the default manifest values and must be
shown in the v2 response or dashboard metadata when the source contributes:

| Source | Attribution / license field |
|---|---|
| OpenAQ | `OpenAQ; underlying station provider as returned by OpenAQ` / source license from OpenAQ license metadata |
| Open-Meteo | `Open-Meteo; model source as documented by Open-Meteo` / deployment-specific terms |
| WorldPop | `WorldPop population estimates` / dataset license from the selected release |
| OpenStreetMap | `© OpenStreetMap contributors` / ODbL |
| ESA WorldCover | `ESA WorldCover` / product terms |
| NASA FIRMS | `NASA FIRMS` / NASA Earth data terms |
| Traffic provider | Provider-specific attribution and retention terms, required before live activation |

The repository's source adapters must fail visibly when a required source is
unavailable. A missing optional source produces a missing feature and quality
flag, not a plausible zero.
