// Turns the stream of run events into the shape the UI draws:
//   data loaded -> Round 1 (analyst, critic, result) -> Round 2 ... -> auditor | error
//
// A pure function, so live streaming, a reconnect, and replaying a finished run all go
// through the same code and produce the same screen.

import type { AnalystOutput, AuditorVerdict, CriticFeedback, FetchDataContent, RunEvent, StreamEnd } from '../api/types'

export interface Round {
  number: number
  analyst?: AnalystOutput
  critic?: CriticFeedback
  /** Set when the round is finished (the consensus_check step). */
  result?: { forcedExit: boolean }
}

export interface RunViewState {
  data?: FetchDataContent
  rounds: Round[]
  auditor?: AuditorVerdict
  error?: string
  forcedExit: boolean
  /** How the stream ended, or null while the run is still live. */
  finished: StreamEnd | null
  /** Highest event id applied; older/duplicate events are ignored (safe replay + resume). */
  lastEventId: number
}

export type RunAction = { type: 'event'; event: RunEvent } | { type: 'end'; status: StreamEnd } | { type: 'reset' }

export const initialRunState: RunViewState = { rounds: [], forcedExit: false, finished: null, lastEventId: 0 }

function updateLastRound(rounds: Round[], update: Partial<Round>): Round[] {
  if (rounds.length === 0) return rounds // critic/result before any analyst: ignore
  const last = rounds[rounds.length - 1]
  return [...rounds.slice(0, -1), { ...last, ...update }]
}

export function runReducer(state: RunViewState, action: RunAction): RunViewState {
  if (action.type === 'reset') return initialRunState
  if (action.type === 'end') return { ...state, finished: action.status }

  const event = action.event
  if (event.id <= state.lastEventId) return state // already applied
  const next = { ...state, lastEventId: event.id }

  switch (event.step) {
    case 'fetch_data':
      return { ...next, data: event.content }
    case 'analyst':
      // Every analyst output starts a new round
      return {
        ...next,
        rounds: [...state.rounds, { number: state.rounds.length + 1, analyst: event.content.analyst_output }],
      }
    case 'critic':
      return { ...next, rounds: updateLastRound(state.rounds, { critic: event.content.critic_feedback }) }
    case 'consensus_check':
      return {
        ...next,
        forcedExit: event.content.forced_exit,
        rounds: updateLastRound(state.rounds, { result: { forcedExit: event.content.forced_exit } }),
      }
    case 'auditor':
      return { ...next, auditor: event.content.auditor_verdict }
    case 'error':
      return { ...next, error: event.content.message }
  }
}

/** A short "what is happening now" line for a live run (null once finished). */
export function currentActivity(state: RunViewState): string | null {
  if (state.finished || state.error) return null
  if (!state.data) return 'Fetching market data and the latest 10-K…'
  const last = state.rounds[state.rounds.length - 1]
  if (!last) return 'Analyst is writing round 1…'
  if (!last.critic) return `Critic is reviewing round ${last.number}…`
  if (!last.result) return `Checking for consensus after round ${last.number}…`
  const debateOver = state.forcedExit || state.rounds.some((r) => r.result && r.critic?.agrees)
  if (debateOver) return state.auditor ? 'Finishing…' : 'Auditor is checking every claim against the evidence…'
  return `Analyst is revising (round ${last.number + 1})…`
}

/** Final outcome of the debate, once the last round has a result. */
export function debateOutcome(state: RunViewState): { kind: 'consensus' | 'unresolved'; rounds: number } | null {
  const last = state.rounds[state.rounds.length - 1]
  if (!last?.result) return null
  if (last.result.forcedExit) return { kind: 'unresolved', rounds: last.number }
  if (last.critic?.agrees) return { kind: 'consensus', rounds: last.number }
  return null // the critic disagreed but more rounds are coming
}

/** "[AAPL-10K-2025-1A-007] (Item 1A - Risk Factors) text" -> { id: {section, text} } */
export function parseExcerpts(chunks: string[]): Record<string, { section: string; text: string }> {
  const excerpts: Record<string, { section: string; text: string }> = {}
  for (const chunk of chunks) {
    const match = /^\[([^\]]+)\]\s*(?:\(([^)]*)\))?\s*([\s\S]*)$/.exec(chunk)
    if (match) excerpts[match[1]] = { section: match[2] ?? '', text: match[3] }
  }
  return excerpts
}
