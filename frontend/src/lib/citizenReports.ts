// Citizen sensor & photo submissions — a seeded set of ground-truth
// reports standing in for the federated edge ingestion pipeline until a
// real submissions endpoint exists. Each entry carries its category,
// time offset and an AI verification score; the map renders these as
// camera pins, and the hex drawer picks the report deterministically for
// the selected cell.
//
// Thumbnails are tiny inline SVGs (baked per report category): they render
// offline, cost no network round-trip, and never send the user anywhere.
// When real photo uploads arrive, swap `thumbnail` for the edge node's URL.

export interface CitizenReport {
  id: string
  name: string
  lat: number
  lon: number
  category: string
  /** How long ago the report was filed, for the "… ago" label. */
  minutesAgo: number
  /** AI verification score in [0, 1]. */
  aiScore: number
}

const REPORT_SEED: CitizenReport[] = [
  { id: 'r1', name: 'Ludhiana periphery', lat: 30.9, lon: 75.85, category: 'Crop Stubble Burning', minutesAgo: 42, aiScore: 0.88 },
  { id: 'r2', name: 'Dadri industrial edge', lat: 28.54, lon: 77.53, category: 'Industrial Smoke', minutesAgo: 23, aiScore: 0.91 },
  { id: 'r3', name: 'Gurugram sector road', lat: 28.42, lon: 77.05, category: 'Open Waste Burning', minutesAgo: 12, aiScore: 0.92 },
  { id: 'r4', name: 'Jaipur ring construction', lat: 26.85, lon: 75.78, category: 'Construction Dust', minutesAgo: 67, aiScore: 0.79 },
  { id: 'r5', name: 'Ahmedabad east landfill', lat: 23.05, lon: 72.62, category: 'Open Waste Burning', minutesAgo: 18, aiScore: 0.94 },
  { id: 'r6', name: 'Vapi corridor roadside', lat: 20.06, lon: 72.87, category: 'Industrial Smoke', minutesAgo: 55, aiScore: 0.84 },
  { id: 'r7', name: 'Kanpur riverbank', lat: 26.45, lon: 80.33, category: 'Open Waste Burning', minutesAgo: 30, aiScore: 0.86 },
  { id: 'r8', name: 'Patna highway shoulder', lat: 25.6, lon: 85.14, category: 'Vehicle Smoke', minutesAgo: 48, aiScore: 0.77 },
  { id: 'r9', name: 'Guwahati brick kilns', lat: 26.16, lon: 91.77, category: 'Industrial Smoke', minutesAgo: 26, aiScore: 0.83 },
  { id: 'r10', name: 'Bhilai steel outskirts', lat: 21.21, lon: 81.35, category: 'Industrial Smoke', minutesAgo: 15, aiScore: 0.9 },
]

export const CITIZEN_REPORTS: CitizenReport[] = REPORT_SEED

export { cameraPinImage }

function cameraPinImage(): ImageData {
  const size = 52
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!

  // White circular pill/badge with a dark drop shadow, so the amber
  // camera reads against the hot orange/red landed palette beneath it.
  const badgeR = 15
  const badgeX = size / 2
  const badgeY = size / 2 - 4
  ctx.save()
  ctx.shadowColor = 'rgba(0, 0, 0, 0.65)'
  ctx.shadowBlur = 5
  ctx.shadowOffsetY = 2
  ctx.beginPath()
  ctx.arc(badgeX, badgeY, badgeR, 0, Math.PI * 2)
  ctx.fillStyle = '#f4f5f7'
  ctx.fill()
  ctx.restore()
  // Hairline ring so the badge survives bright fire-glow adjacency.
  ctx.lineWidth = 1.5
  ctx.strokeStyle = 'rgba(30, 36, 44, 0.55)'
  ctx.stroke()

  // Pin tail
  ctx.beginPath()
  ctx.moveTo(size / 2, size - 8)
  ctx.lineTo(size / 2 - 4, badgeY + badgeR - 3)
  ctx.lineTo(size / 2 + 4, badgeY + badgeR - 3)
  ctx.closePath()
  ctx.fillStyle = '#f4f5f7'
  ctx.shadowColor = 'rgba(0, 0, 0, 0.5)'
  ctx.shadowBlur = 3
  ctx.fill()
  ctx.shadowColor = 'transparent'
  ctx.shadowBlur = 0
  ctx.shadowOffsetY = 0

  // Amber camera glyph inside the badge.
  const bodyW = 15
  const bodyH = 10.5
  const bx = size / 2 - bodyW / 2
  const by = badgeY - bodyH / 2
  ctx.beginPath()
  ctx.roundRect(bx, by, bodyW, bodyH, 2.5)
  ctx.fillStyle = '#f59e0b'
  ctx.fill()
  // Viewfinder bump
  ctx.beginPath()
  ctx.roundRect(size / 2 - 3, by - 3, 6, 4, 1)
  ctx.fill()
  // Lens (dark, punched into the amber body)
  ctx.beginPath()
  ctx.arc(size / 2, badgeY + 0.5, 3.1, 0, Math.PI * 2)
  ctx.fillStyle = '#1e2128'
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}

/** One thumbnail per category — offline-safe inline SVG standing in for the
 *  citizen's uploaded photo. */
function reportThumbnail(category: string): string {
  const palettes: Record<string, [string, string, string]> = {
    'Crop Stubble Burning': ['#3b2f2f', '#e8a13d', '#8a4b2a'],
    'Open Waste Burning': ['#2b2f36', '#f97316', '#5b3a1e'],
    'Industrial Smoke': ['#1f2937', '#9ca3af', '#374151'],
    'Construction Dust': ['#4a3f2f', '#d6b57a', '#7a6a4a'],
    'Vehicle Smoke': ['#232830', '#64748b', '#0f172a'],
  }
  const [sky, smoke, ground] = palettes[category] ?? palettes['Open Waste Burning']
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'>` +
    `<rect width='96' height='96' fill='${sky}'/>` +
    `smoke` +
    `<rect y='64' width='96' height='32' fill='${ground}'/>` +
    `</svg>`
  const smokeCircles = `<circle cx='30' cy='40' r='11' fill='${smoke}' opacity='0.85'/>` +
    `<circle cx='46' cy='30' r='14' fill='${smoke}' opacity='0.65'/>` +
    `<circle cx='62' cy='44' r='9' fill='${smoke}' opacity='0.8'/>`
  const html = svg.replace("smoke", smokeCircles)
  return `data:image/svg+xml;utf8,${encodeURIComponent(html)}`
}

/** The report the drawer shows for a selected hex — deterministic on the
 *  cell id, so re-selecting the same hex shows the same submission. */
export function citizenReportForCell(h3Cell: string): CitizenReport | null {
  if (!h3Cell) return null
  let hash = 0
  for (let i = 0; i < h3Cell.length; i++) {
    hash = (hash * 31 + h3Cell.charCodeAt(i)) >>> 0
  }
  const report = REPORT_SEED[hash % REPORT_SEED.length]
  // Attribute the report to the selected hex's drawer: the citizen report
  // pins sit city-side, but the drawer always displays the closest fused
  // confirmation for the studied hex. The headline example stays 12 mins
  // ago (matches the seeded Gurugram report).
  return {
    ...report,
    name: report.name,
    minutesAgo: report.minutesAgo,
    aiScore: report.aiScore,
  }
}

/** Feature collection of report pins for the map symbol layer. */
export function citizenReportsFeatureCollection(): { features: unknown[]; type: 'FeatureCollection' } {
  return {
    type: 'FeatureCollection',
    features: CITIZEN_REPORTS.map((report) => ({
      type: 'Feature',
      properties: { id: report.id, category: report.category },
      geometry: { type: 'Point', coordinates: [report.lon, report.lat] },
    })),
  }
}

export { reportThumbnail }
