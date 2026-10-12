import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../api/types'
import { COMPARE_EVENTS } from '../test/fixtures'
import { compareReducer, initialCompareState } from './compareReducer'
import { CompareRunView } from './CompareRunView'

const stateFrom = (events: RunEvent[], end?: 'done' | 'failed') => {
  const state = events.reduce((s, event) => compareReducer(s, { type: 'event', event }), initialCompareState)
  return end ? compareReducer(state, { type: 'end', status: end }) : state
}

describe('CompareRunView', () => {
  it('shows the verdict and one tab per company', async () => {
    render(<CompareRunView tickers={['AAPL', 'MSFT']} state={stateFrom(COMPARE_EVENTS, 'done')} />)

    const card = screen.getByRole('heading', { name: 'Comparison' }).closest('article')!
    expect(within(card).getByText('MSFT', { selector: 'strong' })).toBeInTheDocument()
    expect(within(card).getByText('Higher margins at a lower multiple.')).toBeInTheDocument()
    expect(within(card).getByText('Trailing P/E: AAPL 38.6 vs MSFT 29.8')).toBeInTheDocument()
    expect(within(card).getByText('bullish')).toBeInTheDocument()
    expect(within(card).getByText(/this comparison itself is not/)).toBeInTheDocument()

    // Finished: shows the last company researched; clicking a tab switches company
    expect(screen.getByRole('tab', { name: 'MSFT' })).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(screen.getByRole('tab', { name: 'AAPL' }))
    expect(screen.getByRole('tab', { name: 'AAPL' })).toHaveAttribute('aria-selected', 'true')
    expect(within(screen.getByRole('tabpanel')).getByText('Apple Inc.')).toBeInTheDocument()
  })

  it('follows the company being researched and disables tabs not reached yet', () => {
    render(<CompareRunView tickers={['AAPL', 'MSFT']} state={stateFrom(COMPARE_EVENTS.slice(0, 2))} />)
    expect(screen.getByRole('tab', { name: 'AAPL' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'MSFT' })).toBeDisabled()
    expect(screen.getByText(/AAPL: Critic is reviewing round 1/)).toBeInTheDocument()
    expect(screen.getAllByText(/Critic is reviewing/)).toHaveLength(1) // one activity line, not one per tab
    expect(screen.queryByRole('heading', { name: 'Comparison' })).not.toBeInTheDocument()
  })

  it('shows a failed comparison', () => {
    const failed = stateFrom(
      [...COMPARE_EVENTS.slice(0, 2), { id: 50, timestamp: 't', step: 'error', content: { message: 'No market data was found for ZZZZ.' } }],
      'failed',
    )
    render(<CompareRunView tickers={['AAPL', 'ZZZZ']} state={failed} />)
    expect(screen.getByRole('alert')).toHaveTextContent('No market data was found for ZZZZ.')
  })
})
