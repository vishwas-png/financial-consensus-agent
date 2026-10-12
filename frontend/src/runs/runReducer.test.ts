import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../api/types'
import { AGREES, ANALYSIS_1, CONSENSUS_EVENTS, EXCERPT_CHUNKS, FORCED_EXIT_EVENTS, OBJECTS } from '../test/fixtures'
import { currentActivity, debateOutcome, initialRunState, parseExcerpts, runReducer, type RunViewState } from './runReducer'

const apply = (events: RunEvent[], state: RunViewState = initialRunState) =>
  events.reduce((s, event) => runReducer(s, { type: 'event', event }), state)

describe('runReducer', () => {
  it('groups a forced-exit run into rounds', () => {
    const state = runReducer(apply(FORCED_EXIT_EVENTS), { type: 'end', status: 'done' })
    expect(state.data?.company).toBe('Apple Inc.')
    expect(state.rounds.map((r) => r.number)).toEqual([1, 2])
    expect(state.rounds[0]).toMatchObject({ analyst: ANALYSIS_1, critic: OBJECTS, result: { forcedExit: false } })
    expect(state.rounds[1].result).toEqual({ forcedExit: true })
    expect(state.forcedExit).toBe(true)
    expect(state.auditor?.is_grounded).toBe(false)
    expect(state.risk?.overall_risk).toBe('medium')
    expect(state.dataCheck?.mismatched).toBe(1)
    expect(state.finished).toBe('done')
    expect(debateOutcome(state)).toEqual({ kind: 'unresolved', rounds: 2 })
  })

  it('recognises consensus (and runs from before Stage 9 have no risk or data check)', () => {
    const state = apply(CONSENSUS_EVENTS)
    expect(state.risk).toBeUndefined()
    expect(state.dataCheck).toBeUndefined()
    expect(state.rounds).toHaveLength(1)
    expect(state.rounds[0].critic).toEqual(AGREES)
    expect(debateOutcome(state)).toEqual({ kind: 'consensus', rounds: 1 })
  })

  it('has no outcome while more rounds are coming', () => {
    expect(debateOutcome(apply(FORCED_EXIT_EVENTS.slice(0, 4)))).toBeNull() // round 1 done, critic objected
  })

  it('ignores duplicate and older events (safe replay after reconnect)', () => {
    const once = apply(FORCED_EXIT_EVENTS)
    const twice = apply(FORCED_EXIT_EVENTS, once)
    expect(twice).toEqual(once)
    expect(twice.rounds).toHaveLength(2)
  })

  it('live and replayed runs produce the same state', () => {
    const live = apply(FORCED_EXIT_EVENTS.slice(0, 3))
    const resumed = apply(FORCED_EXIT_EVENTS.slice(1), live) // reconnect replays from event 2
    expect(resumed).toEqual(apply(FORCED_EXIT_EVENTS))
  })

  it('records an error event', () => {
    const state = apply([FORCED_EXIT_EVENTS[0], { id: 2, timestamp: 't', step: 'error', content: { message: 'The AI service is busy.' } }])
    expect(state.error).toBe('The AI service is busy.')
    expect(currentActivity(state)).toBeNull()
  })

  it('resets', () => {
    expect(runReducer(apply(CONSENSUS_EVENTS), { type: 'reset' })).toEqual(initialRunState)
  })
})

describe('currentActivity', () => {
  it.each([
    [0, 'Fetching market data'],
    [1, 'Analyst is writing round 1'],
    [2, 'Critic is reviewing round 1'],
    [3, 'Checking for consensus after round 1'],
    [4, 'Analyst is revising (round 2)'],
    [7, 'Risk Officer is weighing'],
    [8, 'Auditor is checking every claim'],
  ])('after %i events says "%s"', (count, expected) => {
    expect(currentActivity(apply(FORCED_EXIT_EVENTS.slice(0, count)))).toContain(expected)
  })

  it('is null once the stream has ended', () => {
    expect(currentActivity(runReducer(apply(CONSENSUS_EVENTS), { type: 'end', status: 'done' }))).toBeNull()
  })
})

describe('parseExcerpts', () => {
  it('maps excerpt ids to their section and text', () => {
    const excerpts = parseExcerpts([...EXCERPT_CHUNKS, 'not an excerpt'])
    expect(Object.keys(excerpts)).toEqual(['AAPL-10K-2025-1A-023', 'AAPL-10K-2025-7-015'])
    expect(excerpts['AAPL-10K-2025-1A-023']).toEqual({
      section: 'Item 1A - Risk Factors',
      text: 'The Company obtains certain components from single or limited sources.',
    })
  })
})
