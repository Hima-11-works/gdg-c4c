import { useCallback, useSyncExternalStore } from 'react'
import {
  INCIDENTS_CHANGED_EVENT,
  findIncident,
  invalidateIncidentCache,
} from '../lib/incidentNotebook'
import type { LocalIncident } from '../lib/incidentNotebook'
import type { ResponseKind } from '../lib/responseTypes'

/**
 * The incident this device holds for a subject.
 *
 * Read through `useSyncExternalStore` rather than state + an effect: the
 * notebook genuinely is an external store (localStorage), and subscribing to
 * it is exactly what that hook is for — an effect that synced state by hand
 * would both re-render twice on mount and need a refresh path for the case
 * where the subject changes. The store returns a cached array, so a snapshot
 * is stable until a write, which is what keeps this from looping.
 *
 * Both events are watched: the store's own event covers writes in this tab,
 * and `storage` covers a write in another tab (where the cache has to be
 * dropped before it can be read).
 */
export function useIncident(kind: ResponseKind, h3Cell: string): LocalIncident | null {
  const subscribe = useCallback((onStoreChange: () => void) => {
    const onStorage = () => {
      invalidateIncidentCache()
      onStoreChange()
    }
    window.addEventListener(INCIDENTS_CHANGED_EVENT, onStoreChange)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(INCIDENTS_CHANGED_EVENT, onStoreChange)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  const getSnapshot = useCallback(() => findIncident(kind, h3Cell), [kind, h3Cell])

  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}
