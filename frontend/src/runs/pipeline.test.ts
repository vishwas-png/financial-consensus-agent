import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../api/types'
import { CONSENSUS_EVENTS, FORCED_EXIT_EVENTS } from '../test/fixtures'
import { formatDuration, parseServerTime, pipelineSteps, stepDurations, totalSeconds } from './pipeline'
import { initialRunState, runReducer, type RunViewState } from './runReducer'

const apply = (events: RunEvent[]): RunViewState =>
  events.reduce((s, event) => runReducer(s, { type: 'event', event }), initialRunState)

const statuses = (state: RunViewState) => Object.fromEntries(pipelineSteps(state).map((s) => [s.key, s.status]))

/** The forced-exit fixture with events 10 s apart, starting 08:00:10 (run created at 08:00:00). */
const TIMED = FORCED_EXIT_EVENTS.map((event, i) => ({
  ...event,
  timestamp: new Date(Date.UTC(2026, 9, 10, 8, 0, (i + 1) * 10)).toISOString(),
}))

describe('pipelineSteps', () => {
  it.each([
    [0, { data: 'active', analyst: 'pending', critic: 'pending', consensus: 'pending', risk: 'pending', auditor: 'pending' }],
    [1, { data: 'done', analyst: 'active', critic: 'pending', consensus: 'pending' }],
    [2, { analyst: 'done', critic: 'active', consensus: 'pending' }],
    [3, { critic: 'done', consensus: 'active' }],
    [4, { analyst: 'active', critic: 'pending', consensus: 'pending' }], // critic objected: round 2 starts
    [7, { analyst: 'done', critic: 'done', consensus: 'done', risk: 'active', auditor: 'pending' }],
    [8, { risk: 'done', auditor: 'active' }],
    [9, { data: 'done', analyst: 'done', critic: 'done', consensus: 'done', risk: 'done', auditor: 'done' }],
  ])('after %i events', (count, expected) => {
    expect(statuses(apply(FORCED_EXIT_EVENTS.slice(0, count)))).toMatchObject(expected)
  })

  it('labels the loop steps with the round once the debate goes past round 1', () => {
    const steps = pipelineSteps(apply(FORCED_EXIT_EVENTS.slice(0, 4)))
    expect(steps.find((s) => s.key === 'analyst')?.note).toBe('round 2')
    expect(pipelineSteps(apply(FORCED_EXIT_EVENTS.slice(0, 2))).find((s) => s.key === 'analyst')?.note).toBeUndefined()
  })

  it('marks the running step as failed when the run fails', () => {
    const failed = apply([
      ...FORCED_EXIT_EVENTS.slice(0, 2),
      { id: 50, timestamp: 't', step: 'error', content: { message: 'busy' } },
    ])
    expect(statuses(failed)).toMatchObject({ analyst: 'done', critic: 'failed', auditor: 'pending' })
  })

  it('does not call a run failed just because the stream stopped waiting', () => {
    const timedOut = runReducer(apply(FORCED_EXIT_EVENTS.slice(0, 2)), { type: 'end', status: 'timeout' })
    expect(statuses(timedOut).critic).toBe('active')
  })

  it('shows the Risk Officer as skipped for runs from before Stage 9', () => {
    expect(statuses(apply(CONSENSUS_EVENTS))).toMatchObject({ risk: 'skipped', auditor: 'done' })
  })
})

describe('timings', () => {
  it('sums the time spent in each step across rounds', () => {
    const durations = stepDurations(apply(TIMED), '2026-10-10T08:00:00Z')
    expect(durations).toEqual({ data: 10, analyst: 20, critic: 20, consensus: 20, risk: 10, auditor: 10 })
    expect(totalSeconds(apply(TIMED), '2026-10-10T08:00:00Z')).toBe(90)
  })

  it('has no time for the first step when the start is unknown', () => {
    expect(stepDurations(apply(TIMED)).data).toBeUndefined()
    expect(totalSeconds(apply(TIMED))).toBeUndefined()
  })

  it('reads server times with and without a timezone as UTC', () => {
    expect(parseServerTime('2026-10-10T08:00:00')).toBe(parseServerTime('2026-10-10T08:00:00Z'))
    expect(parseServerTime('2026-10-10T08:00:00+00:00')).toBe(Date.UTC(2026, 9, 10, 8))
  })

  it.each([[9.4, '9 s'], [59.6, '1:00'], [75, '1:15'], [-3, '0 s']])('formats %s as %s', (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text)
  })
})
