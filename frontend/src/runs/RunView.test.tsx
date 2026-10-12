import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../api/types'
import { CONSENSUS_EVENTS, EXCERPT_CHUNKS, FETCH, FORCED_EXIT_EVENTS } from '../test/fixtures'
import { initialRunState, parseExcerpts, runReducer } from './runReducer'
import { RunView } from './RunView'

const stateFrom = (events: RunEvent[], end?: 'done' | 'failed' | 'timeout') => {
  const state = events.reduce((s, event) => runReducer(s, { type: 'event', event }), initialRunState)
  return end ? runReducer(state, { type: 'end', status: end }) : state
}

describe('RunView', () => {
  it('shows each round with analyst and critic, the unresolved banner and the auditor', () => {
    render(<RunView ticker="AAPL" state={stateFrom(FORCED_EXIT_EVENTS, 'done')} />)

    expect(screen.getByText('Apple Inc.')).toBeInTheDocument()
    const round1 = screen.getByRole('region', { name: 'Round 1' })
    expect(within(round1).getByText(/fairly valued/)).toBeInTheDocument()
    expect(within(round1).getByText('✗ Objects')).toBeInTheDocument()
    expect(within(round1).getByText(/presented as annual/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Round 2' })).toBeInTheDocument()

    expect(screen.getByText(/Unresolved disagreement/)).toBeInTheDocument()
    expect(screen.getByText('✗ Unsupported claims found')).toBeInTheDocument()
    expect(screen.getByText(/revenue_growth is quarterly YoY/)).toBeInTheDocument()
    expect(screen.queryByText(/is checking/)).not.toBeInTheDocument() // finished: no live activity
  })

  it('shows consensus and a grounded verdict', () => {
    render(<RunView ticker="AAPL" state={stateFrom(CONSENSUS_EVENTS, 'done')} />)
    expect(screen.getByText('Consensus reached in round 1.')).toBeInTheDocument()
    expect(screen.getByText('✓ Agrees')).toBeInTheDocument()
    expect(screen.getByText('✓ Grounded in the evidence')).toBeInTheDocument()
  })

  it('shows what is happening while live', () => {
    render(<RunView ticker="AAPL" state={stateFrom(FORCED_EXIT_EVENTS.slice(0, 2))} />)
    expect(screen.getByText(/Critic is reviewing round 1/)).toBeInTheDocument()
  })

  it('expands a citation to its excerpt text once available', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<RunView ticker="AAPL" state={stateFrom(FORCED_EXIT_EVENTS.slice(0, 2))} />)

    await user.click(screen.getByRole('button', { name: 'AAPL-10K-2025-1A-023' }))
    expect(screen.getByText(/shown once the run has finished/)).toBeInTheDocument()

    // When the run finishes and excerpt texts arrive, the already-open citation fills in by itself
    rerender(<RunView ticker="AAPL" state={stateFrom(FORCED_EXIT_EVENTS, 'done')} excerpts={parseExcerpts(EXCERPT_CHUNKS)} />)
    const round1 = screen.getByRole('region', { name: 'Round 1' })
    expect(within(round1).getByText(/single or limited sources/)).toBeInTheDocument()

    // Clicking the open chip again closes it
    await user.click(within(round1).getByRole('button', { name: 'AAPL-10K-2025-1A-023' }))
    expect(within(round1).queryByText(/single or limited sources/)).not.toBeInTheDocument()
  })

  it('shows a failed run', () => {
    const failed = stateFrom([CONSENSUS_EVENTS[0], { id: 2, timestamp: 't', step: 'error', content: { message: 'The AI service is busy (rate limited).' } }], 'failed')
    render(<RunView ticker="AAPL" state={failed} />)
    expect(screen.getByRole('alert')).toHaveTextContent('rate limited')
  })

  it.each([
    ['timeout', /took too long/],
    ['unavailable', /could not be loaded/],
    ['empty', /No 10-K text was found/],
  ] as const)('explains a missing 10-K (%s)', (filingStatus, note) => {
    const fetch = { ...FETCH, content: { ...FETCH.content, filing_excerpts: 0, filing_ids: [], filing_status: filingStatus } } as RunEvent
    render(<RunView ticker="AAPL" state={stateFrom([fetch])} />)
    expect(screen.getByText(note)).toBeInTheDocument()
  })

  it('adds no note when excerpts were found (or the run predates filing_status)', () => {
    render(<RunView ticker="AAPL" state={stateFrom([FETCH])} />)
    expect(screen.queryByText(/market data only/)).not.toBeInTheDocument()
  })

  it('shows the Risk Officer and the data check next to the auditor', () => {
    render(<RunView ticker="AAPL" state={stateFrom(FORCED_EXIT_EVENTS, 'done')} />)
    const risk = screen.getByRole('heading', { name: 'Risk Officer' }).closest('article')!
    expect(within(risk).getByText('medium overall risk')).toBeInTheDocument()
    expect(within(risk).getByText(/Single-source suppliers/)).toBeInTheDocument()
    expect(within(risk).getByText('(AAPL-10K-2025-1A-023)')).toBeInTheDocument()
    expect(within(risk).getByText('(market data)')).toBeInTheDocument()
    expect(within(risk).getByText('revenue_growth turns negative')).toBeInTheDocument()

    const auditor = screen.getByRole('heading', { name: 'Auditor (fact check)' }).closest('article')!
    expect(within(auditor).getByText(/1 of 3 data points match the market data, 1 do not, 1 could not be checked/)).toBeInTheDocument()
    expect(within(auditor).getByText('revenue_growth = 16.4%: the data has revenue_growth = 0.079')).toBeInTheDocument()
  })

  it('renders runs from before Stage 9 without the new cards', () => {
    render(<RunView ticker="AAPL" state={stateFrom(CONSENSUS_EVENTS, 'done')} />)
    expect(screen.queryByRole('heading', { name: 'Risk Officer' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Data check/)).not.toBeInTheDocument()
  })
})
