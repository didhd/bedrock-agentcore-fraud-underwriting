/**
 * SSE client for both backends.
 *
 * Two facts about this transport drive the whole implementation:
 *
 *  1. Both the local demo server and a deployed AgentCore Runtime ALWAYS reply
 *     `text/event-stream`, even for a one-line prompt. So there is one parser.
 *  2. An AgentCore entrypoint that raises mid-stream emits a terminal
 *     `data: {"error": ...}` frame AFTER the response has already committed HTTP
 *     200. A 200 therefore never means the run succeeded, and every frame must be
 *     checked for an `error` key. `onFrame` receives those frames like any other
 *     and the reducer raises them into the UI; nothing here swallows one.
 *
 * `fetch` + a manual parser is used instead of `EventSource` because EventSource
 * cannot send headers (an AgentCore endpoint needs Authorization and
 * X-Amzn-Bedrock-AgentCore-Runtime-Session-Id) and cannot be POSTed.
 */

import type { StreamFrame } from "./types"

export interface StreamOptions {
  /** Called for every parsed frame, including terminal error frames. */
  onFrame: (frame: StreamFrame) => void
  /** Transport-level failure: connection refused, non-2xx, malformed body. */
  onTransportError: (message: string) => void
  signal?: AbortSignal
}

export type BackendKind = "local" | "agentcore"

export interface BackendConfig {
  kind: BackendKind
  /** AgentCore Runtime invocation URL. Only used when kind === "agentcore". */
  endpoint?: string
  /** Bearer token for the AgentCore endpoint. Never persisted. */
  bearerToken?: string
}

/** runtimeSessionId must be at least 33 chars. crypto.randomUUID() is 36. */
export function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID()
  }
  // Fallback: 36 chars of hex-with-dashes, still comfortably over the 33 minimum.
  const hex = Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function requestFor(
  applicationId: string,
  backend: BackendConfig,
  sessionId: string,
): Request | string {
  if (backend.kind === "agentcore") {
    if (!backend.endpoint) {
      throw new Error("AgentCore backend selected but no endpoint URL was provided")
    }
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      // The runtime reads the session id off this header; it must be >= 33 chars.
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
    }
    if (backend.bearerToken) headers.Authorization = `Bearer ${backend.bearerToken}`
    return new Request(backend.endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify({ application_id: applicationId }),
    })
  }
  return `/api/stream/${encodeURIComponent(applicationId)}?backend=local`
}

/**
 * Open the stream and pump frames into `onFrame` until the body ends.
 *
 * Resolves when the stream closes. Never throws for a stream-level error: those
 * arrive as frames, because that is how AgentCore reports them.
 */
export async function streamRun(
  applicationId: string,
  backend: BackendConfig,
  options: StreamOptions,
): Promise<void> {
  const sessionId = newSessionId()
  let response: Response
  try {
    const target = requestFor(applicationId, backend, sessionId)
    response = await fetch(target as RequestInfo, {
      signal: options.signal,
      headers: typeof target === "string" ? { Accept: "text/event-stream" } : undefined,
    })
  } catch (error) {
    if ((error as Error).name === "AbortError") return
    options.onTransportError(`Could not reach the backend: ${(error as Error).message}`)
    return
  }

  if (!response.ok) {
    let detail = ""
    try {
      detail = await response.text()
    } catch {
      detail = ""
    }
    options.onTransportError(
      `Backend returned HTTP ${response.status}${detail ? `: ${detail.slice(0, 400)}` : ""}`,
    )
    return
  }
  if (!response.body) {
    options.onTransportError("Backend returned no response body")
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const flush = (block: string) => {
    // An SSE event may spread its payload over several `data:` lines.
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n")
    if (!data || data === "[DONE]") return
    try {
      options.onFrame(JSON.parse(data) as StreamFrame)
    } catch {
      // Not JSON: a deployed runtime may emit a bare text chunk. Surface it as an
      // error frame rather than dropping it, so nothing is silently lost.
      options.onFrame({ error: `Unparseable frame from backend: ${data.slice(0, 300)}` })
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf("\n\n")
      while (boundary !== -1) {
        flush(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf("\n\n")
      }
    }
    if (buffer.trim()) flush(buffer)
  } catch (error) {
    if ((error as Error).name === "AbortError") return
    options.onTransportError(`Stream interrupted: ${(error as Error).message}`)
  }
}

/** True if a frame carries an error, whatever its `event` value. */
export function frameError(frame: StreamFrame): string | null {
  const candidate = (frame as { error?: unknown }).error
  return typeof candidate === "string" && candidate.length > 0 ? candidate : null
}
