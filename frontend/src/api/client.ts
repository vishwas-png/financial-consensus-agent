// Small fetch wrapper for the backend REST API.
// - adds the base URL and the Google ID token (Authorization: Bearer ...)
// - turns error responses into ApiError with the server's human-readable message
// - calls onUnauthorized() on 401 so the app can sign the user out

import type { Me, RunCreated, RunDetail, RunSummary } from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface ApiClientOptions {
  baseUrl: string
  getToken: () => string | null
  onUnauthorized: () => void
  fetchImpl?: typeof fetch
}

/** FastAPI errors look like {"detail": "text"} or, for validation errors, {"detail": [{msg: ...}]}. */
export async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      return body.detail.map((item: { msg?: string }) => item.msg ?? 'Invalid request').join('; ')
    }
  } catch {
    // not JSON: fall through to a generic message
  }
  return `Request failed (HTTP ${response.status})`
}

export function createApiClient({ baseUrl, getToken, onUnauthorized, fetchImpl = fetch }: ApiClientOptions) {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers)
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (init.body) headers.set('Content-Type', 'application/json')

    const response = await fetchImpl(`${baseUrl}${path}`, { ...init, headers })
    if (response.status === 401) {
      onUnauthorized()
      throw new ApiError(401, 'Your session expired. Please sign in again.')
    }
    if (!response.ok) {
      throw new ApiError(response.status, await errorMessage(response))
    }
    return (await response.json()) as T
  }

  return {
    startResearch: (query: string, maxIterations: number) =>
      request<RunCreated>('/research', {
        method: 'POST',
        body: JSON.stringify({ query, max_iterations: maxIterations }),
      }),
    startComparison: (query: string) =>
      request<RunCreated>('/compare', { method: 'POST', body: JSON.stringify({ query }) }),
    getRun: (runId: string) => request<RunDetail>(`/research/${encodeURIComponent(runId)}`),
    getHistory: () => request<RunSummary[]>('/history'),
    getMe: () => request<Me>('/me'),
  }
}

export type ApiClient = ReturnType<typeof createApiClient>
