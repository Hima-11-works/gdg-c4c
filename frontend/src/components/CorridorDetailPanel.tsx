import { useMapUi } from '../state/MapUiContext'
import type { FreightCorridorProperties } from '../lib/freightCorridors'
import { STATUS_COLORS } from '../lib/freightCorridors'
import { useState } from 'react'

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
      {/* Header */}
      <div className="cell-detail-header corridor-header">
        <div className="cell-detail-titles">
          <div className="corridor-title-row">
            <span
              className={`corridor-badge status-${corridor.status.toLowerCase()}`}
              style={{
                borderColor: statusColor,
                color: statusColor,
                boxShadow: `0 0 10px ${statusColor}33`,
              }}
            >
              {corridor.status.toUpperCase()}
            </span>
            <span className="corridor-code-pill">{corridor.code}</span>
          </div>
          <h2 className="corridor-title">{corridor.name}</h2>
          <span className="corridor-meta-subline">
            Length: {corridor.length_km.toLocaleString()} km · Interstate Freight Spine
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

      {/* High-Visibility AI Logistics & Forecasting Section */}
      <section className="corridor-ai-forecast-section">
        <div className="corridor-section-header">
          <div className="ai-pulse-dot" style={{ backgroundColor: statusColor }} />
          <h3 className="corridor-section-title">AI Logistics & Forecasting</h3>
          <span className="ai-horizon-badge">{corridor.forecast_window}</span>
        </div>

        {/* High-visibility alert banner */}
        <div
          className={`forecast-alert-card alert-${corridor.status.toLowerCase()}`}
          style={{
            borderColor: `${statusColor}88`,
            boxShadow:
              corridor.status === 'Critical'
                ? '0 0 20px rgba(239, 68, 68, 0.25), inset 0 0 15px rgba(239, 68, 68, 0.1)'
                : `0 0 12px ${statusColor}22`,
          }}
        >
          <div className="forecast-alert-header">
            <span className="alert-icon" aria-hidden="true">
              {corridor.status === 'Critical' ? '⚠️' : corridor.status === 'Warning' ? '⚡' : '🛡️'}
            </span>
            <strong className="forecast-alert-heading">
              {corridor.status === 'Critical'
                ? 'CRITICAL PREDICTIVE ALERT'
                : corridor.status === 'Warning'
                  ? 'PREDICTIVE TRANSIT WARNING'
                  : 'OPERATIONAL AI FORECAST'}
            </strong>
          </div>
          <p className="forecast-alert-text">
            {corridor.forecast_alert}
          </p>
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
                  color: corridor.visibility_min_meters < 500 ? '#EF4444' : '#E5E7EB',
                }}
              >
                {corridor.visibility_min_meters}
              </span>
              <span className="metric-unit">meters</span>
            </div>
            <span className="metric-subtext">
              {corridor.visibility_min_meters < 200
                ? 'Severe Dense Haze'
                : corridor.visibility_min_meters < 600
                  ? 'Moderate Fog'
                  : 'Optimal Range'}
            </span>
          </div>

          <div className="corridor-metric-card">
            <span className="metric-label">Est. Freight Delay</span>
            <div className="metric-value-row">
              <span className="metric-value text-amber">{corridor.est_freight_delay}</span>
            </div>
            <span className="metric-subtext">Transit Throttling</span>
          </div>

          <div className="corridor-metric-card">
            <span className="metric-label">Fleet Speed Impact</span>
            <div className="metric-value-row">
              <span
                className="metric-value"
                style={{
                  color: corridor.speed_impact_pct < -30 ? '#EF4444' : '#F59E0B',
                }}
              >
                {corridor.speed_impact_pct}%
              </span>
            </div>
            <span className="metric-subtext">Cargo Velocity Delta</span>
          </div>
        </div>
      </section>

      {/* Interstate Interoperability & Governance Section */}
      <section className="corridor-interstate-section">
        <div className="corridor-section-header">
          <span className="interstate-icon">🌐</span>
          <h3 className="corridor-section-title">Interstate Interoperability & Airshed</h3>
        </div>
        <p className="corridor-interstate-desc">
          Transboundary pollution and freight logistics span across state jurisdictions. Coordinated
          multi-state protocols prevent upstream spillover:
        </p>

        {/* Affected States List */}
        <div className="corridor-states-container">
          <span className="states-list-label">AFFECTED STATES ({corridor.affected_states.length}):</span>
          <div className="corridor-states-pills">
            {corridor.affected_states.map((state) => (
              <span key={state} className="corridor-state-pill">
                <span className="state-pin">📍</span>
                {state}
              </span>
            ))}
          </div>
        </div>

        {/* Coordination protocol box */}
        <div className="corridor-coordination-card">
          <div className="coordination-icon-badge">🤝</div>
          <div className="coordination-content">
            <strong>Inter-State Joint Protocol</strong>
            <p>{corridor.interstate_coordination}</p>
          </div>
        </div>
      </section>

      {/* Corridor Key Nodes & Logistics Spine */}
      <section className="corridor-nodes-section">
        <div className="corridor-section-header">
          <span className="nodes-icon">🛤️</span>
          <h3 className="corridor-section-title">Strategic Multi-Modal Hubs</h3>
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
                {index === 0 && <span className="node-tag">Origin Gateway</span>}
                {index === corridor.key_nodes.length - 1 && (
                  <span className="node-tag">Terminus Gateway</span>
                )}
                {node.includes('Kanpur') && (
                  <span className="node-tag-alert">Spike Center (+14h)</span>
                )}
                {node.includes('Varanasi') && (
                  <span className="node-tag-alert">High Inversion</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Operational Logistics Advisory */}
      <section className="corridor-advisory-section">
        <div className="corridor-section-header">
          <span className="advisory-icon">📋</span>
          <h3 className="corridor-section-title">Logistics Operational Directive</h3>
        </div>
        <p className="corridor-advisory-body">{corridor.recommended_action}</p>

        <div className="corridor-actions-bar">
          <button
            type="button"
            className={`btn-corridor-action ${protocolActivated ? 'btn-active' : ''}`}
            onClick={handleTriggerProtocol}
          >
            {protocolActivated
              ? '✓ Interstate Alert Broadcasted'
              : 'Trigger Interstate Green Corridor'}
          </button>
          <button
            type="button"
            className="btn-corridor-action btn-secondary"
            onClick={handleCopyAdvisory}
          >
            {advisoryCopied ? '✓ Copied Advisory' : 'Copy Multi-State Advisory'}
          </button>
        </div>
      </section>
    </aside>
  )
}
