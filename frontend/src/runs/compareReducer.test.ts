import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../api/types'
import { COMPARE_EVENTS, COMPARISON, CONSENSUS_EVENTS, FETCH } from '../test/fixtures'
import { compareReducer, currentCompareActivity, initialCompareState, type CompareViewState } from './compareReducer'

const apply = (events: RunEvent[], state: CompareViewState = initialCompareState) =>
  events.reduce((s, event) => compareReducer(s, { type: 'event', event }), state)

const PER_COMPANY = CONSENSUS_EVENTS.length // events per company; the comparator comes last

describe('compareReducer', () => {
  it('splits events by company and keeps the comparison', () => {
    const state = apply(COMPARE_EVENTS)
    expect(state.tickers).toEqual(['AAPL', 'MSFT'])
    expect(state.perTicker.AAPL.data?.company).toBe('Apple Inc.')
    expect(state.perTicker.AAPL.rounds).toHaveLength(1)
    expect(state.perTicker.MSFT.auditor?.is_grounded).toBe(true)
    expect(state.comparison).toEqual(COMPARISON)
    expect(state.perTicker.AAPL.finished).toBe('done') // finished once the comparator arrived
  })

  it('marks a company finished when the next one starts', () => {
    const state = apply(COMPARE_EVENTS.slice(0, PER_COMPANY + 1)) // AAPL done, MSFT's first event
    expect(state.perTicker.AAPL.finished).toBe('done')
    expect(state.perTicker.MSFT.finished).toBeNull()
  })

  it('ignores duplicates and replays identically after a reconnect', () => {
    const live = apply(COMPARE_EVENTS.slice(0, 7))
    const resumed = apply(COMPARE_EVENTS.slice(3), live)
    expect(resumed).toEqual(apply(COMPARE_EVENTS))
  })

  it('records an error and the end of the stream', () => {
    const failed = apply([
      ...COMPARE_EVENTS.slice(0, 2),
      { id: 99, timestamp: 't', step: 'error', content: { message: 'No market data was found for ZZZZ.' } },
    ])
    expect(failed.error).toMatch(/ZZZZ/)
    const ended = compareReducer(failed, { type: 'end', status: 'failed' })
    expect(ended.finished).toBe('failed')
    expect(ended.perTicker.AAPL.finished).toBe('failed')
  })

  it('ignores untagged per-step events (single-run streams)', () => {
    expect(apply([FETCH]).tickers).toEqual([]) // FETCH has no ticker tag
  })
})

describe('currentCompareActivity', () => {
  const tickers = ['AAPL', 'MSFT']

  it.each([
    [0, /^Starting with AAPL/],
    [2, /^AAPL: Critic is reviewing round 1/],
    [PER_COMPANY - 1, /^AAPL: Risk Officer is weighing/],
    [PER_COMPANY, /^Moving on to MSFT/],
    [PER_COMPANY + 1, /^MSFT: Analyst is writing round 1/],
    [2 * PER_COMPANY, /^Comparator is weighing AAPL vs MSFT/],
  ])('after %i events', (count, expected) => {
    expect(currentCompareActivity(apply(COMPARE_EVENTS.slice(0, count)), tickers)).toMatch(expected)
  })

  it('is null once the comparison arrived', () => {
    expect(currentCompareActivity(apply(COMPARE_EVENTS), tickers)).toBeNull()
  })
})
