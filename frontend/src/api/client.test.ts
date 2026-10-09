import { describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from './client'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function makeClient(response: Response, token: string | null = 'tok-123') {
  const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => response)
  const onUnauthorized = vi.fn()
  const client = createApiClient({ baseUrl: 'http://api', getToken: () => token, onUnauthorized, fetchImpl })
  return { client, fetchImpl, onUnauthorized }
}

describe('createApiClient', () => {
  it('sends the Bearer token and JSON body to POST /research', async () => {
    const { client, fetchImpl } = makeClient(jsonResponse(202, { run_id: 'r1', ticker: 'AAPL', status: 'pending' }))
    const created = await client.startResearch('How is Apple doing?', 2)

    expect(created).toEqual({ run_id: 'r1', ticker: 'AAPL', status: 'pending' })
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('http://api/research')
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer tok-123')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init?.body as string)).toEqual({ query: 'How is Apple doing?', max_iterations: 2 })
  })

  it('omits the Authorization header when signed out', async () => {
    const { client, fetchImpl } = makeClient(jsonResponse(200, []), null)
    await client.getHistory()
    expect(new Headers(fetchImpl.mock.calls[0][1]?.headers).has('Authorization')).toBe(false)
  })

  it('signs out on 401', async () => {
    const { client, onUnauthorized } = makeClient(jsonResponse(401, { detail: 'Invalid or expired Google ID token' }))
    await expect(client.getMe()).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it.each([
    [429, { detail: 'Daily limit reached: 5/5 research runs in the last 24 hours.' }, 'Daily limit reached: 5/5'],
    [422, { detail: "Couldn't identify a single US-listed company" }, 'US-listed company'],
    [503, { detail: 'The AI service is busy. Please try again in a minute.' }, 'AI service is busy'],
    [422, { detail: [{ msg: 'String should have at least 1 character' }] }, 'at least 1 character'],
  ])('shows the server message for HTTP %i', async (status, body, expected) => {
    const { client, onUnauthorized } = makeClient(jsonResponse(status, body))
    const error = await client.startResearch('x', 1).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(status)
    expect((error as ApiError).message).toContain(expected)
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('falls back to a generic message for non-JSON errors', async () => {
    const { client } = makeClient(new Response('<html>Bad gateway</html>', { status: 502 }))
    await expect(client.getHistory()).rejects.toThrow('Request failed (HTTP 502)')
  })

  it('encodes run ids in the URL', async () => {
    const { client, fetchImpl } = makeClient(jsonResponse(200, {}))
    await client.getRun('a/b')
    expect(fetchImpl.mock.calls[0][0]).toBe('http://api/research/a%2Fb')
  })
})
