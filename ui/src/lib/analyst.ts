/**
 * The analyst identity this browser sends as the memory actor.
 *
 * WHY THIS EXISTS AT ALL
 *
 * `ui/chat.py` already carries an `analyst_id` all the way through to a signed
 * `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id` header, and Francis's session manager
 * scopes long-term memory to `/analysts/{actor_id}/facts`. But the browser never sent one,
 * so every conversation from the UI pooled into the shared default actor -- the exact
 * failure the custom header was added to avoid. Actor-scoped memory was implemented,
 * deployed, and then not reachable from the product.
 *
 * WHAT THIS IS NOT
 *
 * It is NOT authentication. It is a stable label so two browsers are two actors and
 * per-analyst memory is demonstrable. Anyone can change it; nothing is authorised by it.
 * Real identity is Lab 4's Cognito path: a JWT whose `username` claim becomes the actor,
 * with `authorizerType: CUSTOM_JWT` on the runtime. Until that is wired, this must be
 * labelled in the UI as a demo identity rather than presented as a signed-in user, because
 * a page that shows an unauthenticated string in a position where users expect an account
 * is worse than showing nothing.
 *
 * WHY localStorage, when BackendBar deliberately refuses it
 *
 * BackendBar keeps the bearer token in component state precisely because it is a
 * credential. This is the opposite: it authorises nothing, and it is only useful if it
 * SURVIVES a reload -- memory written under one actor is unreachable if the next page load
 * invents a new one. The hardened-profile guard is copied from theme-provider.tsx, where
 * localStorage is already known to throw.
 */

const STORAGE_KEY = "pp-fraud-analyst-id"

/** Readable rather than a bare uuid: this string appears in traces and in memory namespaces. */
function mint(): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.floor(Math.random() * 0xffffffff).toString(16)
  return `analyst-${suffix}`
}

export function loadAnalystId(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return stored
  } catch {
    // Hardened profile. Fall through and mint a per-session id: memory will not carry
    // across reloads, which is a degraded feature rather than a broken page.
    return mint()
  }
  const minted = mint()
  try {
    localStorage.setItem(STORAGE_KEY, minted)
  } catch {
    // Same as above.
  }
  return minted
}

export function saveAnalystId(value: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // Same as above.
  }
}

/**
 * `analyst_id` is capped at 120 characters by the FastAPI route and truncated to 120 in
 * `ui/chat.py`. Normalising here means the value shown in the UI is the value actually sent,
 * rather than a longer string that is silently cut on the way out.
 */
export function normaliseAnalystId(value: string): string {
  return value.trim().replace(/\s+/g, "-").slice(0, 120)
}
