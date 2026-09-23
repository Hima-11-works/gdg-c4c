// Static geometry for the map's zoom-dependent detail layers, alongside
// lib/stateBoundaries.ts's state/UT polygons and country outline.
//
// District borders - geoBoundaries ADM2 for India, simplified with mapshaper
// (5% keep-shapes, then 4-decimal coordinates) from the release's own
// simplified file: 735 districts in 439 KB, smaller than the ADM1 file next to
// it. License: ODC-ODbL, attribution geoBoundaries, William & Mary geoLab -
// the same terms as the ADM1 file.
//
// Major highways - Natural Earth 10m roads, clipped to the country outline and
// filtered to its "Major Highway" class: 111 corridors for India, 104 KB.
// Natural Earth is public domain (no attribution required, credited anyway).
// This is NE's coarse major-road selection, NOT the full Indian National
// Highway network - it names the main corridors, and most individual NH routes
// are not in it. A complete NH layer needs an OpenStreetMap extract (Geofabrik
// India, ~1 GB) filtered to motorway/trunk and simplified with osmium or
// tippecanoe, which is a build step rather than an asset.
//
// Both are draw-only: nothing here is parsed for point-in-polygon lookups, so
// they stay plain URLs for MapLibre's GeoJSON sources.
//
// How they were built, so the assets can be regenerated:
//
//   npx mapshaper geoBoundaries-IND-ADM2_simplified.geojson \
//     -simplify 5% keep-shapes -rename-fields name=shapeName \
//     -filter-fields name -o india_districts.geojson precision=0.0001
//
//   npx mapshaper ne_10m_roads.shp -filter 'type == "Major Highway"' \
//     -clip india_country.geojson -o india_highways.geojson precision=0.0001

export const DISTRICT_BOUNDARIES_URL = '/data/india_districts.geojson'
export const MAJOR_HIGHWAYS_URL = '/data/india_highways.geojson'
