import { useMapUi } from '../state/MapUiContext'

interface MapActionToolbarProps {
  onReport: () => void
  onReviewPhotos: () => void
  reportAvailable: boolean
  inline?: boolean
}

export function MapActionToolbar({
  onReport,
  onReviewPhotos,
  reportAvailable,
  inline = false,
}: MapActionToolbarProps) {
  const { state, dispatch } = useMapUi()
  const evidenceLabel = state.hotspotPanelOpen
    ? 'Hide fire candidate evidence'
    : 'Show fire candidate evidence'

  return (
    <div
      className={`map-action-toolbar ${inline ? 'map-action-toolbar-inline' : ''}`}
      role="toolbar"
      aria-label="Map actions"
    >
      <button
        type="button"
        className="panel map-action-button"
        onClick={onReport}
        aria-label="Report fire"
        aria-haspopup="dialog"
        disabled={!reportAvailable}
        title={reportAvailable ? 'Report fire' : 'Waiting for map location'}
        data-tooltip={reportAvailable ? 'Report fire' : 'Waiting for map location'}
      >
        <svg
          className="map-action-fire-icon"
          viewBox="0 0 24 24"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M12.1 22c4.2 0 7.4-3.1 7.4-7.2 0-2.6-1.3-4.5-3.5-6.7-.2 2-1.1 3-2.2 3.5.1-3.5-1.7-6.3-5.2-9.6.1 4.1-1.1 6.4-3 8.8-1.2 1.5-1.9 3-1.9 4.8 0 3.8 3.1 6.4 8.4 6.4Z" />
          <path d="M12 19.2c1.8 0 3-1.2 3-2.8 0-1.1-.6-2-1.8-3.2-.2 1.1-.6 1.6-1.3 1.9-.2-1.2-.8-2-1.8-2.9 0 1.4-.4 2.3-1 3.1-.4.5-.6 1-.6 1.5 0 1.4 1.3 2.4 3.5 2.4Z" />
        </svg>
      </button>
      <button
        type="button"
        className="panel map-action-button"
        onClick={onReviewPhotos}
        aria-label="Review citizen photos"
        aria-haspopup="dialog"
        title="Review citizen photos"
        data-tooltip="Review citizen photos"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M4 7.5h3l1.4-2h7.2l1.4 2h3A1.5 1.5 0 0 1 21.5 9v9.2a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5V9A1.5 1.5 0 0 1 4 7.5Z" />
          <circle cx="12" cy="13" r="3.5" />
          <path d="M17.5 10h.01" />
        </svg>
      </button>
      <button
        type="button"
        className={`panel map-action-button hotspot-toggle-btn ${state.hotspotPanelOpen ? 'active' : ''}`}
        onClick={() => {
          if (!state.showHotspotCandidates) dispatch({ type: 'TOGGLE_HOTSPOT_CANDIDATES' })
          dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
        }}
        aria-expanded={state.hotspotPanelOpen}
        aria-controls="hotspot-evidence-panel"
        aria-label={evidenceLabel}
        title={evidenceLabel}
        data-tooltip={evidenceLabel}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="12" cy="12" r="3" />
          <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" />
        </svg>
      </button>
    </div>
  )
}
