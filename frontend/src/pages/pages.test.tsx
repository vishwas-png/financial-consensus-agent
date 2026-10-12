import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent } from '../api/types'
import { AppRoutes } from '../App'
import { AuthProvider } from '../auth/AuthProvider'
import { clearToken, saveToken } from '../auth/token'
import { EXCERPT_CHUNKS, FORCED_EXIT_EVENTS, METRICS } from '../test/fixtures'
import { makeToken } from '../test/tokens'
import { HistoryPage } from './HistoryPage'
import { NewResearchPage } from './NewResearchPage'
import { RunPage } from './RunPage'

// ---------- fetch mock: "METHOD /path" -> Response factory ----------

type Handler = (init?: RequestInit) => Response
let routes: Record<string, Handler> = {}
const calls: { key: string; init?: RequestInit }[] = []

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function sseResponse(events: RunEvent[], endStatus = 'done'): Response {
  const text =
    events.map((e) => `id: ${e.id}\nevent: ${e.step}\ndata: ${JSON.stringify(e)}\n\n`).join('') +
    `event: end\ndata: {"status": "${endStatus}"}\n\n`
  return new Response(text, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

beforeEach(() => {
  routes = {}
  calls.length = 0
  saveToken(makeToken())
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    const key = `${init?.method ?? 'GET'} ${url.pathname}`
    calls.push({ key, init })
    const handler = routes[key]
    return handler ? handler(init) : json(404, { detail: 'Not found' })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  clearToken()
})

function renderAt(path: string, element: React.ReactNode, routePath = path) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePath} element={element} />
          <Route path="/runs/:runId" element={<p>Run page for test</p>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

const ME = { id: 'alice-sub', email: 'alice@example.com', quota: { runs_last_24h: 2, runs_per_day: 5, active_runs: 0, max_active_runs: 1, window_hours: 24 } }

// ---------- App shell ----------

describe('AppRoutes', () => {
  it('shows only the login page when signed out', () => {
    clearToken()
    render(<AuthProvider><MemoryRouter><AppRoutes /></MemoryRouter></AuthProvider>)
    expect(screen.getByRole('heading', { name: 'Financial Consensus Agent' })).toBeInTheDocument()
    expect(screen.getByTestId('google-button')).toBeInTheDocument()
    expect(screen.queryByText('History')).not.toBeInTheDocument()
  })

  it('shows the header with the user and signs out', async () => {
    routes['GET /me'] = () => json(200, ME)
    render(<AuthProvider><MemoryRouter><AppRoutes /></MemoryRouter></AuthProvider>)
    expect(screen.getByText(/alice@example.com/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(screen.getByTestId('google-button')).toBeInTheDocument()
  })
})

// ---------- New research ----------

describe('NewResearchPage', () => {
  it('starts a run and navigates to it', async () => {
    routes['GET /me'] = () => json(200, ME)
    routes['POST /research'] = () => json(202, { run_id: 'run-1', ticker: 'AAPL', status: 'pending' })
    renderAt('/', <NewResearchPage />)

    expect(await screen.findByText(/2\/5 runs used/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Company or question'), 'How is Apple doing?')
    await userEvent.selectOptions(screen.getByLabelText('Maximum debate rounds'), '1')
    await userEvent.click(screen.getByRole('button', { name: 'Start research' }))

    expect(await screen.findByText('Run page for test')).toBeInTheDocument()
    const post = calls.find((c) => c.key === 'POST /research')!
    expect(JSON.parse(post.init?.body as string)).toEqual({ query: 'How is Apple doing?', max_iterations: 1 })
    expect(new Headers(post.init?.headers).get('Authorization')).toMatch(/^Bearer /)
  })

  it.each([
    [429, 'Daily limit reached: 5/5 research runs in the last 24 hours.'],
    [422, "Couldn't identify a single US-listed company in your request."],
  ])('shows the server message on HTTP %i', async (status, detail) => {
    routes['GET /me'] = () => json(200, ME)
    routes['POST /research'] = () => json(status, { detail })
    renderAt('/', <NewResearchPage />)
    await userEvent.click(screen.getByRole('button', { name: 'NVDA' })) // example chip fills the query
    await userEvent.click(screen.getByRole('button', { name: 'Start research' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
  })

  it('signs out when the API says the token is invalid', async () => {
    routes['GET /me'] = () => json(401, { detail: 'Invalid or expired Google ID token' })
    render(<AuthProvider><MemoryRouter><AppRoutes /></MemoryRouter></AuthProvider>)
    expect(await screen.findByTestId('google-button')).toBeInTheDocument()
  })
})

// ---------- Run page ----------

describe('RunPage', () => {
  const detail = (status: string, finalOutput: unknown = null) => ({
    id: 'run-1', ticker: 'AAPL', status, created_at: '2026-10-10T08:00:00Z', final_output: finalOutput, events: [],
  })

  it('streams the run, then loads excerpts for citations', async () => {
    let getCount = 0
    routes['GET /research/run-1'] = () => {
      getCount += 1
      return json(200, getCount === 1
        ? detail('running')
        : { ...detail('done', { filing_chunks: EXCERPT_CHUNKS }), metrics: METRICS })
    }
    routes['GET /research/run-1/stream'] = () => sseResponse(FORCED_EXIT_EVENTS)
    renderAt('/runs/run-1', <RunPage />, '/runs/:runId')

    expect(await screen.findByText(/Unresolved disagreement/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Research: AAPL' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Round 2' })).toBeInTheDocument()
    expect(screen.getByText('✗ Unsupported claims found')).toBeInTheDocument()

    await waitFor(() => expect(getCount).toBe(2))
    const round1 = screen.getByRole('region', { name: 'Round 1' })
    await userEvent.click(within(round1).getByRole('button', { name: 'AAPL-10K-2025-1A-023' }))
    expect(within(round1).getByText(/single or limited sources/)).toBeInTheDocument()

    expect(screen.getByText('Run details')).toBeInTheDocument() // metrics panel once finished

    const stream = calls.find((c) => c.key === 'GET /research/run-1/stream')!
    expect(new Headers(stream.init?.headers).get('Authorization')).toMatch(/^Bearer /)
  })

  it('shows not found for an unknown or foreign run', async () => {
    renderAt('/runs/someone-elses', <RunPage />, '/runs/:runId')
    expect(await screen.findByRole('alert')).toHaveTextContent('Run not found.')
  })
})

// ---------- History ----------

describe('HistoryPage', () => {
  it('lists runs with links', async () => {
    routes['GET /history'] = () => json(200, [
      { id: 'run-2', ticker: 'MSFT', status: 'done', created_at: '2026-10-10T09:00:00Z', outcome: 'unresolved_disagreement', thesis: 'Microsoft is fairly valued.' },
      { id: 'run-1', ticker: 'AAPL', status: 'failed', created_at: '2026-10-10T08:00:00Z', outcome: null, thesis: null },
    ])
    renderAt('/history', <HistoryPage />)
    const links = await screen.findAllByRole('link')
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['/runs/run-2', '/runs/run-1'])
    expect(screen.getByText('Unresolved disagreement')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('has an empty state', async () => {
    routes['GET /history'] = () => json(200, [])
    renderAt('/history', <HistoryPage />)
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument()
  })
})
