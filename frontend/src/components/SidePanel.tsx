// A floating panel that pops in and out from a single arrow button, used by
// the legend (top right, collapses to the right) and the settings & layer
// checklist (bottom left, collapses downwards).
//
// The body is absolutely positioned against the button rather than sitting
// next to it in the flow, so collapsing slides the panel away without
// reflowing anything around it — the button stays put, and the panels next
// to it (search bar, timeline) never move. Visibility is part of the
// transition so a collapsed panel is also out of the tab order, not just
// transparent.

import type { ReactNode } from 'react'

export type SidePanelSide = 'right' | 'up'

interface SidePanelProps {
  id: string
  side: SidePanelSide
  open: boolean
  onToggle: () => void
  /** Accessible name of the toggle, phrased as the action it performs. */
  label: string
  children: ReactNode
}

export function SidePanel({ id, side, open, onToggle, label, children }: SidePanelProps) {
  return (
    <div className={`side-panel side-panel-${side} ${open ? 'is-open' : 'is-closed'}`}>
      <div className="side-panel-body" id={id} aria-hidden={!open}>
        {children}
      </div>
      <button
        type="button"
        className="side-panel-toggle"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={id}
        aria-label={label}
        title={label}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path
            d={
              side === 'right'
                ? open
                  ? 'M9 5l7 7-7 7'
                  : 'M15 5l-7 7 7 7'
                : open
                  ? 'M5 9l7 7 7-7'
                  : 'M5 15l7-7 7 7'
            }
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  )
}
