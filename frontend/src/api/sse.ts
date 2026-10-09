// Server-Sent Events over fetch().
//
// Why not the browser's EventSource? It cannot send an Authorization header, and we never put
// the Google ID token in a URL (it would end up in server logs). So we read the response body
// as a stream and parse the SSE text format ourselves.
//
// SSE format: messages are separated by a blank line; each line is "field: value".
//   id: 42
//   event: analyst
//   data: {"id": 42, "step": "analyst", ...}
// Lines starting with ":" are comments (our server sends ": heartbeat").

import { ApiError, errorMessage } from './client'
import type { RunEvent, StreamEnd } from './types'

export interface SSEMessage {
  id?: string
  event: string
  data: string
}

/** Incremental parser: network chunks can split a message anywhere, so keep the unfinished tail. */
export class SSEParser {
  private buffer = ''

  feed(chunk: string): SSEMessage[] {
    this.buffer += chunk.replace(/\r\n?/g, '\n')
    const messages: SSEMessage[] = []
    let boundary = this.buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const block = this.buffer.slice(0, boundary)
      this.buffer = this.buffer.slice(boundary + 2)
      const message = parseBlock(block)
      if (message) messages.push(message)
      boundary = this.buffer.indexOf('\n\n')
    }
    return messages
  }
}

function parseBlock(block: string): SSEMessage | null {
  let id: string | undefined
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line === '' || line.startsWith(':')) continue // comment / heartbeat
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'id') id = value
    else if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  if (data.length === 0 && id === undefined) return null // a block of only comments
  return { id, event, data: data.join('\n') }
}

export interface StreamRunOptions {
  baseUrl: string
  runId: string
  getToken: () => string | null
  onEvent: (event: RunEvent) => void
  signal?: AbortSignal
  maxRetries?: number
  retryDelayMs?: number
  fetchImpl?: typeof fetch
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Stream a run's events until the server sends `end`. Returns how the run ended.
 * If the connection drops, it reconnects with Last-Event-ID so no event is lost or repeated.
 * Throws ApiError for HTTP errors (401, 404, ...) and AbortError if `signal` is aborted.
 */
export async function streamRun({
  baseUrl,
  runId,
  getToken,
  onEvent,
  signal,
  maxRetries = 3,
  retryDelayMs = 1000,
  fetchImpl = fetch,
}: StreamRunOptions): Promise<StreamEnd> {
  let lastEventId: string | undefined
  let failures = 0

  while (true) {
    const headers = new Headers({ Accept: 'text/event-stream' })
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (lastEventId) headers.set('Last-Event-ID', lastEventId)

    try {
      const response = await fetchImpl(`${baseUrl}/research/${encodeURIComponent(runId)}/stream`, { headers, signal })
      if (!response.ok) throw new ApiError(response.status, await errorMessage(response))
      if (!response.body) throw new Error('Streaming is not supported by this browser')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      const parser = new SSEParser()
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        for (const message of parser.feed(decoder.decode(value, { stream: true }))) {
          if (message.id) lastEventId = message.id
          if (message.event === 'end') return (JSON.parse(message.data) as { status: StreamEnd }).status
          onEvent(JSON.parse(message.data) as RunEvent)
          failures = 0 // progress was made; reset the retry budget
        }
      }
      // The body ended without an `end` event: the connection dropped. Reconnect below.
    } catch (error) {
      if (signal?.aborted || error instanceof ApiError) throw error
      // network error: fall through to the retry logic
    }

    failures += 1
    if (failures > maxRetries) throw new Error('Lost connection to the live stream. Reload the page to resume.')
    await sleep(retryDelayMs * failures)
  }
}
