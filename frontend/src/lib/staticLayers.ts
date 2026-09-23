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
// Major roads - the same source's next class down (`type == "Road"`), same
// clip: 155 lines in 56 KB. NE's Road class sits at the same scaleranks as its
// Major Highways (3-4), which is what makes "major roads" a fair description;
// its unranked entries are the ones this deliberately leaves out. Like the
// highways, this is a coarse selection, not a secondary-road network.
//
// Highways are draw-only: the URL goes straight to a MapLibre GeoJSON source.
// The district file is both drawn and parsed - lib/stateBoundaries.ts loads it
// on demand to clip a district-scoped search to a real border (via
// hooks/useDistrictBoundaries), the same way it parses the ADM1 file.
//
// How they were built, so the assets can be regenerated:
//
//   npx mapshaper geoBoundaries-IND-ADM2_simplified.geojson \
//     -simplify 5% keep-shapes -rename-fields name=shapeName \
//     -filter-fields name -o india_districts.geojson precision=0.0001
//
//   npx mapshaper ne_10m_roads.shp -filter 'type == "Major Highway"' \
//     -clip india_country.geojson -o india_highways.geojson precision=0.0001
//
//   npx mapshaper ne_10m_roads.shp -filter 'type == "Road"' \
//     -clip india_country.geojson -simplify 60% keep-shapes \
//     -filter-fields name,type -o india_major_roads.geojson precision=0.0001

export const DISTRICT_BOUNDARIES_URL = '/data/india_districts.geojson'
export const MAJOR_HIGHWAYS_URL = '/data/india_highways.geojson'
export const MAJOR_ROADS_URL = '/data/india_major_roads.geojson'
