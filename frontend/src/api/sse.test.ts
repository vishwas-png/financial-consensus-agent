import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { SSEParser, streamRun } from './sse'
import type { RunEvent } from './types'

describe('SSEParser', () => {
  it('parses id, event and data', () => {
    const parser = new SSEParser()
    expect(parser.feed('id: 7\nevent: analyst\ndata: {"a":1}\n\n')).toEqual([
      { id: '7', event: 'analyst', data: '{"a":1}' },
    ])
  })

  it('handles a message split across chunks', () => {
    const parser = new SSEParser()
    expect(parser.feed('id: 1\nevent: crit')).toEqual([])
    expect(parser.feed('ic\ndata: {"x"')).toEqual([])
    expect(parser.feed(':2}\n')).toEqual([])
    expect(parser.feed('\n')).toEqual([{ id: '1', event: 'critic', data: '{"x":2}' }])
  })

  it('handles several messages in one chunk and CRLF line endings', () => {
    const parser = new SSEParser()
    const messages = parser.feed('event: a\r\ndata: 1\r\n\r\nevent: b\ndata: 2\n\n')
    expect(messages.map((m) => [m.event, m.data])).toEqual([['a', '1'], ['b', '2']])
  })

  it('ignores heartbeat comments and joins multi-line data', () => {
    const parser = new SSEParser()
    expect(parser.feed(': heartbeat\n\n')).toEqual([])
    expect(parser.feed('data: line1\ndata: line2\n\n')).toEqual([{ id: undefined, event: 'message', data: 'line1\nline2' }])
  })
})

// ---------- streamRun ----------

function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

function sse(id: number, step: string, content: object): string {
  const data = { id, step, content, timestamp: '2026-10-10T00:00:00Z' }
  return `id: ${id}\nevent: ${step}\ndata: ${JSON.stringify(data)}\n\n`
}

const END_DONE = 'event: end\ndata: {"status": "done"}\n\n'

describe('streamRun', () => {
  it('delivers events in order and returns the end status', async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) =>
      new Response(sseBody([sse(1, 'analyst', {}), ': heartbeat\n\n', sse(2, 'critic', {}), END_DONE]), { status: 200 }))
    const events: RunEvent[] = []
    const end = await streamRun({ baseUrl: 'http://api', runId: 'r1', getToken: () => 'tok', onEvent: (e) => events.push(e), fetchImpl })

    expect(end).toBe('done')
    expect(events.map((e) => [e.id, e.step])).toEqual([[1, 'analyst'], [2, 'critic']])
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('http://api/research/r1/stream')
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer tok')
  })

  it('reconnects with Last-Event-ID after a dropped connection', async () => {
    const responses = [
      new Response(sseBody([sse(1, 'fetch_data', {}), sse(2, 'analyst', {})]), { status: 200 }), // drops, no `end`
      new Response(sseBody([sse(3, 'critic', {}), 'event: end\ndata: {"status": "failed"}\n\n']), { status: 200 }),
    ]
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => responses.shift()!)
    const events: RunEvent[] = []
    const end = await streamRun({
      baseUrl: 'http://api', runId: 'r1', getToken: () => 'tok', onEvent: (e) => events.push(e), fetchImpl, retryDelayMs: 0,
    })

    expect(end).toBe('failed')
    expect(events.map((e) => e.id)).toEqual([1, 2, 3])
    expect(new Headers(fetchImpl.mock.calls[0][1]?.headers).has('Last-Event-ID')).toBe(false)
    expect(new Headers(fetchImpl.mock.calls[1][1]?.headers).get('Last-Event-ID')).toBe('2')
  })

  it('gives up after maxRetries failed connections', async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => {
      throw new TypeError('Failed to fetch')
    })
    await expect(
      streamRun({ baseUrl: 'http://api', runId: 'r1', getToken: () => 't', onEvent: () => {}, fetchImpl, maxRetries: 2, retryDelayMs: 0 }),
    ).rejects.toThrow('Lost connection')
    expect(fetchImpl).toHaveBeenCalledTimes(3)
  })

  it('does not retry HTTP errors like 404', async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) =>
      new Response(JSON.stringify({ detail: 'Run not found' }), { status: 404 }))
    const error = await streamRun({ baseUrl: 'http://api', runId: 'x', getToken: () => 't', onEvent: () => {}, fetchImpl }).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('stops when aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetchImpl = vi.fn(async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
      if (init?.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      return new Response(sseBody([END_DONE]))
    })
    await expect(
      streamRun({ baseUrl: 'http://api', runId: 'r1', getToken: () => 't', onEvent: () => {}, fetchImpl, signal: controller.signal }),
    ).rejects.toThrow('Aborted')
    expect(fetchImpl).toHaveBeenCalledOnce()
  })
})
