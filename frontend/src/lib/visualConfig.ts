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
