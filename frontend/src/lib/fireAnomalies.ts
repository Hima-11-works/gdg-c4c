/** FRP bands used for a rough review triage only, not a fire classification. */
export type FireSeverity = 1 | 2 | 3

export const FRP_SEVERITY_BANDS: ReadonlyArray<{ max: number; severity: FireSeverity }> = [
  { max: 20, severity: 1 },
  { max: 50, severity: 2 },
  { max: Infinity, severity: 3 },
]

export function severityForFrp(frp: number): FireSeverity {
  for (const band of FRP_SEVERITY_BANDS) {
    if (frp < band.max) return band.severity
  }
  return 3
}

/** A review-queue priority only. A satellite detection never confirms a fire. */
export function priorityForSeverity(severity: FireSeverity): 1 | 2 | 3 {
  return (4 - severity) as 1 | 2 | 3
}
