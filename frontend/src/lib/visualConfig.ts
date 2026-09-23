// Centralized visual configuration for the pollution grid overlay.
// Every tunable visual constant lives here so opacity, transitions,
// and border styling can be adjusted from one place.

/** Fill opacity for the PM2.5 hex grid layer. */
export const PM25_FILL_OPACITY = 0.62

/** Fill opacity for the PDI hex grid layer. */
export const PDI_FILL_OPACITY = 0.6

/** Width of unselected hex cell borders (subtle, quiet). */
export const CELL_BORDER_WIDTH = 0.5

/** Color of unselected hex cell borders. */
export const CELL_BORDER_COLOR = '#00000025'

/** Width of the selected cell's highlight border. */
export const SELECTED_CELL_BORDER_WIDTH = 2

/** Color of the selected cell's highlight border. */
export const SELECTED_CELL_BORDER_COLOR = '#ffffffcc'

/** Contrast mode: line drawn on the boundary between PM2.5 bands. Dark and
 *  crisp so same-range regions read as separated blocks regardless of the
 *  cell colors underneath. */
export const CONTRAST_LINE_COLOR = '#05080c'
export const CONTRAST_LINE_WIDTH = 1.6
export const CONTRAST_LINE_OPACITY = 0.85

/** District borders (level 2 and finer). Dimmer than the state/UT dashes they
 *  sit under, so a dense district mesh never competes with them. */
export const DISTRICT_BOUNDARY_COLOR = '#333944'

/** Major highways (level 3 and finer). Drawn as a road does it: a dark casing
 *  under a light core, because a single flat colour can't read over all eight
 *  PM2.5 band colours at once - warm amber disappeared into the orange and red
 *  bands. The casing carries the contrast, so the line is legible over green,
 *  yellow, orange and red alike. */
export const HIGHWAY_CORE_COLOR = '#ffd9a0'
export const HIGHWAY_CASING_COLOR = '#241a0c'

/** Major roads (level 4 and finer). Deliberately quieter than a highway: one
 *  thin line instead of a cased pair, so a dense local road mesh reads as
 *  context under the highways rather than competing with them. Still light
 *  enough to sit legibly over every PM2.5 band. */
export const MAJOR_ROAD_COLOR = '#f0e4cd'

/** Duration (ms) of the opacity dissolve between forecast frames. Long
 *  enough to read as a continuous flow at the 750ms playback cadence (each
 *  frame dissolves into the next), short enough to leave a moment of hold. */
export const PM25_DISSOLVE_DURATION_MS = 550

/** Duration (ms) of opacity crossfade when toggling PM2.5 / PDI layers. */
export const LAYER_CROSSFADE_DURATION_MS = 250

/** Frames per second cap for transition animations. */
export const TRANSITION_FPS = 30

/** Interval (ms) between transition animation frames. */
export const TRANSITION_FRAME_INTERVAL_MS = 1000 / TRANSITION_FPS

/**
 * Whether the user has requested reduced motion.
 * Cached on first read — the media query rarely changes mid-session.
 */
let _reducedMotion: boolean | null = null

export function prefersReducedMotion(): boolean {
  if (_reducedMotion === null) {
    _reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  }
  return _reducedMotion
}
