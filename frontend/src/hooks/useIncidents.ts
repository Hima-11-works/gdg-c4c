import { useCallback, useEffect, useState } from 'react'
import {
  fetchIncidentDeliveries,
  fetchIncidentHistory,
  fetchIncidents,
} from '../lib/api'
import { useApiResource } from './useApiResource'
import type { IncidentDeliveryOut, IncidentEventOut, IncidentOut } from '../lib/types'

/** How often the dashboard re-reads incidents, so a responder's update made in
 *  the app shows up here without a manual refresh. */
export const INCIDENT_POLL_MS = 10_000

/**
 * Every incident the service knows about, polled.
 *
 * Reads are public, so this works with no credentials at all — a read-only
 * deployment still sees the real workflow rather than a local stand-in. The
 * list is small and the panel needs to match on the source key, so one list
 * read serves every open panel.
 */
export function useIncidents() {
  return useApiResource(() => fetchIncidents(), [], {
    pollIntervalMs: INCIDENT_POLL_MS,
  })
}

/** One incident's append-only history, polled alongside the list.
 *
 * "Loading" is *derived* from which incident the snapshot belongs to, rather
 * than written from inside the effect: writing state synchronously there would
 * start a second render on every mount. Same reason the snapshot carries the id
 * it was read for — a poll landing after the panel moved on must not be shown as
 * the new incident's history. */
export function useIncidentHistory(incidentId: number | null): {
  events: IncidentEventOut[]
  loading: boolean
  error: string | null
} {
  const [snapshot, setSnapshot] = useState<{
    id: number | null
    events: IncidentEventOut[]
    error: string | null
  }>({ id: null, events: [], error: null })

  useEffect(() => {
    if (incidentId === null) return
    let cancelled = false
    const read = async () => {
      try {
        const envelope = await fetchIncidentHistory(incidentId)
        if (cancelled) return
        setSnapshot({ id: incidentId, events: envelope.data, error: null })
      } catch (cause) {
        if (cancelled) return
        setSnapshot({
          id: incidentId,
          events: [],
          error: cause instanceof Error ? cause.message : 'Could not read the history.',
        })
      }
    }
    void read()
    const timer = window.setInterval(() => void read(), INCIDENT_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [incidentId])

  if (incidentId === null) return { events: [], loading: false, error: null }
  return {
    events: snapshot.id === incidentId ? snapshot.events : [],
    loading: snapshot.id !== incidentId,
    error: snapshot.id === incidentId ? snapshot.error : null,
  }
}

/**
 * The simulated delivery records for an incident.
 *
 * Kept apart from the history because what they show is the hand-off itself:
 * every row is `simulated: true` and says no notification was sent, and the UI
 * has to be able to say that in those words rather than implying a dispatch.
 */
export function useIncidentDeliveries(incidentId: number | null): {
  deliveries: IncidentDeliveryOut[]
  error: string | null
} {
  const [snapshot, setSnapshot] = useState<{
    id: number | null
    deliveries: IncidentDeliveryOut[]
    error: string | null
  }>({ id: null, deliveries: [], error: null })

  useEffect(() => {
    if (incidentId === null) return
    let cancelled = false
    const read = async () => {
      try {
        const envelope = await fetchIncidentDeliveries(incidentId)
        if (cancelled) return
        setSnapshot({ id: incidentId, deliveries: envelope.data, error: null })
      } catch (cause) {
        if (cancelled) return
        setSnapshot({
          id: incidentId,
          deliveries: [],
          error: cause instanceof Error ? cause.message : 'Could not read the deliveries.',
        })
      }
    }
    void read()
    const timer = window.setInterval(() => void read(), INCIDENT_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [incidentId])

  if (incidentId === null) return { deliveries: [], error: null }
  return {
    deliveries: snapshot.id === incidentId ? snapshot.deliveries : [],
    error: snapshot.id === incidentId ? snapshot.error : null,
  }
}

/** Re-read the list on demand, after a write. */
export function useIncidentRefresher(): () => void {
  const { refetch } = useIncidents()
  return useCallback(() => refetch(), [refetch])
}

export type { IncidentOut }
