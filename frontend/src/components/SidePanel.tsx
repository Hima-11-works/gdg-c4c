// A floating panel that pops in and out from a single arrow button, used by
// the legend (top left, collapses to the left), the settings & layer
// checklist (bottom left, collapses downwards) and the published-run status
// (top right, collapses to the right).
//
// The body is absolutely positioned against the button rather than sitting
// next to it in the flow, so collapsing slides the panel away without
// reflowing anything around it — the button stays put, and the panels next
// to it never move. Visibility is part of the transition so a collapsed
// panel is also out of the tab order, not just transparent.

import type { ReactNode } from 'react'

export type SidePanelSide = 'left' | 'right' | 'up'

interface SidePanelProps {
  id: string
  side: SidePanelSide
  open: boolean
  onToggle: () => void
  /** Accessible name of the toggle, phrased as the action it performs. */
  label: string
  children: ReactNode
}

/** The chevron points the way the panel will move when the button is
 *  clicked: away from its edge when the panel is out (it will be pushed
 *  back), toward the edge when it is away (it will be pulled back out). */
const CHEVRON: Record<SidePanelSide, { open: string; closed: string }> = {
  left: { open: 'M15 5l-7 7 7 7', closed: 'M9 5l7 7-7 7' },
  right: { open: 'M9 5l7 7-7 7', closed: 'M15 5l-7 7 7 7' },
  up: { open: 'M5 9l7 7 7-7', closed: 'M5 15l7-7 7 7' },
}

export function SidePanel({ id, side, open, onToggle, label, children }: SidePanelProps) {
  return (
    <div className={`side-panel side-panel-${side} ${open ? 'is-open' : 'is-closed'}`}>
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
            d={open ? CHEVRON[side].open : CHEVRON[side].closed}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <div className="side-panel-body" id={id} aria-hidden={!open}>
        {children}
      </div>
    </div>
  )
}
