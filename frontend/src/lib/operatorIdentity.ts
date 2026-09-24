// Which responder this browser is acting as, for incident writes.
//
// The incident API needs two things on every write: the deployment's simulator
// key, and an `X-Actor-Id` naming a responder. The key authenticates the
// *deployment*; the actor header names *who within it* is acting. The server
// resolves that actor's role and jurisdiction from its own registry, so
// nothing chosen here can widen authority — an unregistered actor is refused
// with 401, and a wrong role with 403.
//
// Reads need neither. The dashboard is fully usable as a viewer with no
// credentials at all; only the create/assign controls are gated on this.

const ACTOR_OVERRIDE_KEY = 'air-health:simulator-actor'

/** The deployment's simulator key. Empty means writes are not configured. */
export const SIMULATOR_API_KEY: string = import.meta.env.VITE_SIMULATOR_API_KEY ?? ''

/** The actor id used unless the operator overrides it for this session. */
export const CONFIGURED_ACTOR_ID: string = import.meta.env.VITE_SIMULATOR_ACTOR_ID ?? ''

/**
 * Actor ids offered as a convenience list.
 *
 * This is a *hint*, not an authority: it is display configuration, the server
 * decides whether any of these exist and what they may do. It exists so an
 * operator can be told the shape of the registry without reading source.
 */
export const SUGGESTED_ACTOR_IDS: string[] = (
  import.meta.env.VITE_SIMULATOR_ACTOR_IDS ?? ''
)
  .split(',')
  .map((value: string) => value.trim())
  .filter((value: string) => value !== '')

export function writesAreConfigured(): boolean {
  return SIMULATOR_API_KEY !== '' && actorId() !== ''
}

/** The actor this browser acts as right now. */
export function actorId(): string {
  try {
    const override = window.sessionStorage.getItem(ACTOR_OVERRIDE_KEY)
    if (override !== null && override.trim() !== '') return override.trim()
  } catch {
    // Session storage unavailable: fall through to the configured value.
  }
  return CONFIGURED_ACTOR_ID
}

export function setActorOverride(value: string | null): void {
  try {
    if (value === null || value.trim() === '') {
      window.sessionStorage.removeItem(ACTOR_OVERRIDE_KEY)
    } else {
      window.sessionStorage.setItem(ACTOR_OVERRIDE_KEY, value.trim())
    }
  } catch {
    // Nothing to do: the override is a convenience, and the configured value
    // still works.
  }
}

/** Why writes are unavailable, when they are. */
export function writesUnavailableReason(): string | null {
  if (SIMULATOR_API_KEY === '') {
    return (
      'No simulator key is configured for this dashboard, so incident writes are unavailable. ' +
      'Reading incidents still works — those routes are public.'
    )
  }
  if (actorId() === '') {
    return (
      'No actor is configured for this dashboard, so incident writes are unavailable. ' +
      'Set one below or start the dev server with VITE_SIMULATOR_ACTOR_ID.'
    )
  }
  return null
}
