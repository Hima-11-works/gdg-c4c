import { useState } from 'react'
import { useMapUi } from '../state/MapUiContext'
import type { FreightCorridorProperties } from '../lib/freightCorridors'
import { STATUS_COLORS } from '../lib/freightCorridors'

interface CorridorDetailPanelProps {
  corridor: FreightCorridorProperties
}

export function CorridorDetailPanel({ corridor }: CorridorDetailPanelProps) {
  const { dispatch } = useMapUi()
  const [protocolActivated, setProtocolActivated] = useState(false)
  const [advisoryCopied, setAdvisoryCopied] = useState(false)

  const close = () => dispatch({ type: 'SELECT_CORRIDOR', corridor: null })

  const statusColor = STATUS_COLORS[corridor.status] || '#00F5D4'

  const handleTriggerProtocol = () => {
    setProtocolActivated(true)
    setTimeout(() => setProtocolActivated(false), 5000)
  }

  const handleCopyAdvisory = () => {
    navigator.clipboard?.writeText?.(
      `[INTERSTATE CORRIDOR ADVISORY - ${corridor.code}]\n${corridor.forecast_alert}\nAffected States: ${corridor.affected_states.join(', ')}\nAction: ${corridor.recommended_action}`,
    )
    setAdvisoryCopied(true)
    setTimeout(() => setAdvisoryCopied(false), 2500)
  }

  return (
    <aside className="panel cell-detail corridor-detail" aria-label="Major Freight Corridor Details">
      {/* Header matching CellDetailPanel */}
      <div className="cell-detail-header">
        <div className="cell-detail-titles">
          <h2>{corridor.name}</h2>
          <span className="cell-detail-index">
            {corridor.code} · {corridor.length_km.toLocaleString()} km · Interstate Freight Spine
          </span>
        </div>
        <button
          type="button"
          className="cell-detail-close"
          onClick={close}
          aria-label="Close corridor details"
          title="Close"
        >
          ✕
        </button>
      </div>

      {/* Priority triage badge matching CellDetailPanel */}
      <div
        className={`priority-badge priority-${
          corridor.status === 'Critical' ? '3' : corridor.status === 'Warning' ? '2' : '1'
        }`}
        role="status"
      >
        <span className="priority-dot" aria-hidden="true" />
        <span>
          <strong>{corridor.status.toUpperCase()} CORRIDOR ALERT</strong>
          {' · '}
          <span className="muted">{corridor.forecast_window} window</span>
        </span>
      </div>

      {/* Transparent illustrative note matching repo guidelines */}
      <div className="corridor-demo-notice" role="status">
        <span aria-hidden="true" className="demo-notice-icon">ⓘ</span>
        <span>Illustrative predictive corridor scenario — not live statutory directives or official freight tolls.</span>
      </div>

      {/* AI Logistics & Forecasting Section */}
      <section className="corridor-section">
        <div className="corridor-section-header">
          <h3>AI Logistics & Forecasting</h3>
          <span className="corridor-horizon-badge">{corridor.forecast_window}</span>
        </div>

        {/* Status card styled consistently with grap-card / alert-item */}
        <div className={`corridor-alert-card corridor-alert-${corridor.status.toLowerCase()}`}>
          <div className="corridor-alert-header">
            <svg
              className="corridor-card-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              focusable="false"
            >
              {corridor.status === 'Critical' ? (
                <>
                  <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                  <line x1="12" y1="9" x2="12" y2="13" />
                  <line x1="12" y1="17" x2="12.01" y2="17" />
                </>
              ) : corridor.status === 'Warning' ? (
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
              ) : (
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              )}
            </svg>
            <strong>
              {corridor.status === 'Critical'
                ? 'Critical Predictive Alert'
                : corridor.status === 'Warning'
                  ? 'Predictive Transit Warning'
                  : 'Operational AI Forecast'}
            </strong>
          </div>
          <p className="corridor-alert-text">{corridor.forecast_alert}</p>
        </div>

        {/* Key Predictive Metrics Grid */}
        <div className="corridor-metrics-grid">
          <div className="corridor-metric-card">
            <span className="metric-label">Forecast PM2.5 / AQI</span>
            <div className="metric-value-row">
              <span className="metric-value" style={{ color: statusColor }}>
                {corridor.air_quality_index_forecast}
              </span>
              <span className="metric-unit">AQI</span>
            </div>
            <span className="metric-subtext">{corridor.primary_pollutant}</span>
          </div>

          <div className="corridor-metric-card">
            <span className="metric-label">Min. Visibility</span>
            <div className="metric-value-row">
              <span
                className="metric-value"
                style={{
                  color: corridor.visibility_min_meters < 500 ? '#f87171' : '#e5e7eb',
                }}
              >
                {corridor.visibility_min_meters}
              </span>
              <span className="metric-unit">m</span>
            </div>
            <span className="metric-subtext">
              {corridor.visibility_min_meters < 200
                ? 'Dense haze'
                : corridor.visibility_min_meters < 600
                  ? 'Moderate fog'
                  : 'Optimal range'}
            </span>
          </div>

          <div className="corridor-metric-card">
            <span className="metric-label">Est. Freight Delay</span>
            <div className="metric-value-row">
              <span className="metric-value text-amber">{corridor.est_freight_delay}</span>
            </div>
            <span className="metric-subtext">Transit throttling</span>
          </div>

          <div className="corridor-metric-card">
            <span className="metric-label">Fleet Speed Impact</span>
            <div className="metric-value-row">
              <span
                className="metric-value"
                style={{
                  color: corridor.speed_impact_pct < -30 ? '#f87171' : '#fbbf24',
                }}
              >
                {corridor.speed_impact_pct}%
              </span>
            </div>
            <span className="metric-subtext">Velocity delta</span>
          </div>
        </div>
      </section>

      {/* Interstate Interoperability & Airshed Section */}
      <section className="corridor-section">
        <div className="corridor-section-header">
          <svg
            className="corridor-card-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="2" y1="12" x2="22" y2="12" />
            <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
          </svg>
          <h3>Interstate Interoperability & Airshed</h3>
        </div>
        <p className="muted" style={{ margin: '0.2rem 0 0.55rem' }}>
          Transboundary pollution and freight logistics span across state jurisdictions. Coordinated
          multi-state protocols prevent upstream spillover:
        </p>

        {/* Affected States List */}
        <div className="corridor-states-container">
          <span className="states-list-label">
            AFFECTED STATES ({corridor.affected_states.length})
          </span>
          <div className="corridor-states-pills">
            {corridor.affected_states.map((state) => (
              <span key={state} className="corridor-state-pill">
                <span className="state-dot" aria-hidden="true" />
                {state}
              </span>
            ))}
          </div>
        </div>

        {/* Coordination protocol card styled like grap-card */}
        <div className="grap-card" style={{ margin: '0.6rem 0 0' }}>
          <div
            className="grap-card-title"
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              focusable="false"
            >
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
            Inter-State Joint Protocol
          </div>
          <p className="grap-card-advisory" style={{ margin: '0.2rem 0 0' }}>
            {corridor.interstate_coordination}
          </p>
        </div>
      </section>

      {/* Corridor Strategic Hubs */}
      <section className="corridor-section">
        <div className="corridor-section-header">
          <svg
            className="corridor-card-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
          >
            <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
            <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
          </svg>
          <h3>Strategic Multi-Modal Hubs</h3>
        </div>
        <div className="corridor-nodes-list">
          {corridor.key_nodes.map((node, index) => (
            <div key={node} className="corridor-node-item">
              <div className="node-marker-col">
                <div
                  className={`node-dot ${
                    index === 0 || index === corridor.key_nodes.length - 1
                      ? 'node-dot-terminal'
                      : ''
                  }`}
                />
                {index < corridor.key_nodes.length - 1 && <div className="node-line" />}
              </div>
              <div className="node-text-col">
                <span className="node-name">{node}</span>
                {index === 0 && <span className="node-tag">Origin</span>}
                {index === corridor.key_nodes.length - 1 && (
                  <span className="node-tag">Terminus</span>
                )}
                {node.includes('Kanpur') && (
                  <span className="node-tag-alert">Spike (+14h)</span>
                )}
                {node.includes('Varanasi') && (
                  <span className="node-tag-alert">Inversion</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Operational Logistics Advisory */}
      <section className="corridor-section" style={{ marginBottom: '0.75rem' }}>
        <div className="corridor-section-header">
          <svg
            className="corridor-card-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <polyline points="10 9 9 9 8 9" />
          </svg>
          <h3>Logistics Operational Directive</h3>
        </div>
        <p className="muted" style={{ margin: '0.2rem 0 0', lineHeight: 1.45 }}>
          {corridor.recommended_action}
        </p>
      </section>

      {/* Sticky action bar matching CellDetailPanel's intervention-bar */}
      <div className="intervention-bar" role="group" aria-label="Corridor actions">
        <button
          type="button"
          className={`intervention-cta ${
            corridor.status === 'Critical'
              ? ''
              : corridor.status === 'Warning'
                ? 'intervention-cta-inspect'
                : 'intervention-cta-dispatch'
          } ${protocolActivated ? 'intervention-cta-done' : ''}`}
          onClick={handleTriggerProtocol}
        >
          {protocolActivated
            ? '✓ Interstate Alert Broadcasted'
            : 'Trigger Interstate Green Corridor'}
        </button>
        <button
          type="button"
          className={`intervention-cta intervention-cta-log ${
            advisoryCopied ? 'intervention-cta-done' : ''
          }`}
          onClick={handleCopyAdvisory}
          style={{ marginTop: '0.4rem' }}
        >
          {advisoryCopied ? '✓ Copied Multi-State Advisory' : 'Copy Multi-State Advisory'}
        </button>
        <p className="intervention-status">
          {protocolActivated
            ? 'Simulated on this device only. Nothing was sent outside this browser session.'
            : advisoryCopied
              ? 'Copied. Dispatch through official transit authority channels.'
              : 'Multi-jurisdiction advisory for transport departments and state boards.'}
        </p>
      </div>
    </aside>
  )
}
