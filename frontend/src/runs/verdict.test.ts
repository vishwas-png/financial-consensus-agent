import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../api/types'
import { CONSENSUS_EVENTS, FORCED_EXIT_EVENTS } from '../test/fixtures'
import { initialRunState, runReducer, type RunViewState } from './runReducer'
import { buildVerdict } from './verdict'

const apply = (events: RunEvent[]): RunViewState =>
  events.reduce((s, event) => runReducer(s, { type: 'event', event }), initialRunState)

describe('buildVerdict', () => {
  it('summarises a finished Stage 9 run', () => {
    const verdict = buildVerdict(apply(FORCED_EXIT_EVENTS))!
    expect(verdict.analysis.thesis).toMatch(/modestly overvalued/) // the FINAL round's analysis
    expect(verdict.facts.map((f) => [f.label, f.value, f.tone, f.href])).toEqual([
      ['Debate', 'Unresolved after 2 rounds', 'warn', '#debate'],
      ['Confidence', 'low', 'warn', '#debate'], // round 2 lowered its confidence
      ['Fact check', '1 unsupported claim', 'warn', '#auditor'],
      ['Numbers', '1/2 match the data', 'warn', '#auditor'],
      ['Risk', 'medium overall', 'neutral', '#risk'],
    ])
    expect(verdict.topRisk).toMatch(/Single-source suppliers/)
  })

  it('works for runs from before Stage 9 (no risk, no data check)', () => {
    const verdict = buildVerdict(apply(CONSENSUS_EVENTS))!
    expect(verdict.facts.map((f) => [f.label, f.value, f.tone])).toEqual([
      ['Debate', 'Consensus in round 1', 'ok'],
      ['Confidence', 'medium', 'neutral'],
      ['Fact check', 'Grounded in the evidence', 'ok'],
    ])
    expect(verdict.topRisk).toBeUndefined()
  })

  it('is null until there is an audited final analysis', () => {
    expect(buildVerdict(initialRunState)).toBeNull()
    expect(buildVerdict(apply(FORCED_EXIT_EVENTS.slice(0, 8)))).toBeNull() // risk done, auditor not yet
  })

  it('is null for a failed run', () => {
    const failed = apply([...FORCED_EXIT_EVENTS, { id: 99, timestamp: 't', step: 'error', content: { message: 'x' } }])
    expect(buildVerdict(failed)).toBeNull()
  })

  it('says when no number could be checked', () => {
    const state = apply(FORCED_EXIT_EVENTS)
    const noneCheckable = { ...state, dataCheck: { checks: [state.dataCheck!.checks[2]], matched: 0, mismatched: 0, unchecked: 1 } }
    expect(buildVerdict(noneCheckable)!.facts.find((f) => f.label === 'Numbers')?.value).toBe('None checkable')
  })
})
