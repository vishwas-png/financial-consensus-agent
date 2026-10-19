// The progress pipeline: Data -> Analyst -> Critic -> Consensus -> Risk Officer -> Auditor.
// Pure functions from RunViewState, so live, replayed and finished runs all show the same thing.

import type { RunEvent } from '../api/types'
import type { RunViewState } from './runReducer'

export type StepStatus = 'done' | 'active' | 'pending' | 'failed' | 'skipped'

export interface PipelineStep {
  key: 'data' | 'analyst' | 'critic' | 'consensus' | 'risk' | 'auditor'
  label: string
  status: StepStatus
  /** Total seconds spent in this step (all rounds), once at least one pass has finished. */
  seconds?: number
  /** e.g. "round 2" while the debate loop is on a later round. */
  note?: string
}

/** Which pipeline step each streamed event completes. */
const EVENT_STEP: Partial<Record<RunEvent['step'], PipelineStep['key']>> = {
  fetch_data: 'data',
  analyst: 'analyst',
  critic: 'critic',
  consensus_check: 'consensus',
  risk_officer: 'risk',
  auditor: 'auditor',
}

/** Server timestamps are UTC; SQLite (local dev) returns them without a zone, so add one. */
export function parseServerTime(value: string): number {
  return Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`)
}

/** 75 -> "1:15", 9.4 -> "9 s" */
export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds))
  if (whole < 60) return `${whole} s`
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Seconds spent per pipeline step: each event's time minus the previous event's (or the start). */
export function stepDurations(state: RunViewState, startedAt?: string): Partial<Record<PipelineStep['key'], number>> {
  const totals: Partial<Record<PipelineStep['key'], number>> = {}
  let previous = startedAt ? parseServerTime(startedAt) : undefined
  for (const { step, at } of state.stepTimes) {
    const time = parseServerTime(at)
    const key = EVENT_STEP[step]
    if (key && previous !== undefined && Number.isFinite(time) && Number.isFinite(previous)) {
      totals[key] = (totals[key] ?? 0) + Math.max(0, (time - previous) / 1000)
    }
    previous = time
  }
  return totals
}

export function pipelineSteps(state: RunViewState, startedAt?: string): PipelineStep[] {
  const last = state.rounds[state.rounds.length - 1]
  const debateOver = state.forcedExit || state.rounds.some((r) => r.result && r.critic?.agrees)
  const revising = Boolean(last?.result) && !debateOver // critic objected, another round is coming
  const round = revising ? state.rounds.length + 1 : state.rounds.length

  const status: Record<PipelineStep['key'], StepStatus> = {
    data: state.data ? 'done' : 'active',
    analyst: !state.data ? 'pending' : debateOver || (last && !revising) ? 'done' : 'active',
    critic: !last || revising ? 'pending' : debateOver || last.critic ? 'done' : 'active',
    consensus: debateOver ? 'done' : last?.critic && !last.result ? 'active' : 'pending',
    risk: state.risk ? 'done' : debateOver && !state.auditor ? 'active' : state.auditor ? 'skipped' : 'pending',
    auditor: state.auditor ? 'done' : debateOver && state.risk ? 'active' : 'pending',
  }
  // (risk 'skipped' = a run from before Stage 9: the auditor followed the debate directly)

  // A stream that merely stopped waiting ('timeout') is not a failed run
  if (state.error || state.finished === 'failed') {
    // The step that was running when the run stopped is the one that failed
    for (const key of Object.keys(status) as PipelineStep['key'][]) {
      if (status[key] === 'active') status[key] = 'failed'
    }
  }

  const durations = stepDurations(state, startedAt)
  const labels: Record<PipelineStep['key'], string> = {
    data: 'Data',
    analyst: 'Analyst',
    critic: 'Critic',
    consensus: 'Consensus',
    risk: 'Risk Officer',
    auditor: 'Auditor',
  }
  return (Object.keys(labels) as PipelineStep['key'][]).map((key) => ({
    key,
    label: labels[key],
    status: status[key],
    seconds: durations[key],
    note: ['analyst', 'critic', 'consensus'].includes(key) && round > 1 ? `round ${round}` : undefined,
  }))
}

/** Seconds from the start to the last event (a finished run's total), if known. */
export function totalSeconds(state: RunViewState, startedAt?: string): number | undefined {
  const lastAt = state.stepTimes[state.stepTimes.length - 1]?.at
  if (!startedAt || !lastAt) return undefined
  return Math.max(0, (parseServerTime(lastAt) - parseServerTime(startedAt)) / 1000)
}
