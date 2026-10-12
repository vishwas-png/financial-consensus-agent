// Turns a comparison run's events into: one RunViewState per company + the comparison verdict.
//
// Each company's steps go through the normal runReducer, so a company's debate renders exactly
// like a single research run. Companies are researched one after another, so when events for
// the next company (or the comparator) arrive, the earlier companies are marked finished.

import { eventTicker, type ComparisonOutput, type RunEvent, type StreamEnd } from '../api/types'
import { currentActivity, initialRunState, runReducer, type RunViewState } from './runReducer'

export interface CompareViewState {
  /** Companies in the order they were researched (as their events arrive). */
  tickers: string[]
  perTicker: Record<string, RunViewState>
  comparison?: ComparisonOutput
  error?: string
  finished: StreamEnd | null
  lastEventId: number
}

export type CompareAction = { type: 'event'; event: RunEvent } | { type: 'end'; status: StreamEnd } | { type: 'reset' }

export const initialCompareState: CompareViewState = { tickers: [], perTicker: {}, finished: null, lastEventId: 0 }

function finishAll(perTicker: Record<string, RunViewState>, status: StreamEnd): Record<string, RunViewState> {
  return Object.fromEntries(
    Object.entries(perTicker).map(([ticker, state]) => [ticker, state.finished ? state : { ...state, finished: status }]),
  )
}

export function compareReducer(state: CompareViewState, action: CompareAction): CompareViewState {
  if (action.type === 'reset') return initialCompareState
  if (action.type === 'end') {
    return { ...state, finished: action.status, perTicker: finishAll(state.perTicker, action.status) }
  }

  const event = action.event
  if (event.id <= state.lastEventId) return state // already applied (replay after reconnect)
  const next = { ...state, lastEventId: event.id }

  if (event.step === 'comparator') {
    return { ...next, comparison: event.content.comparison, perTicker: finishAll(state.perTicker, 'done') }
  }
  if (event.step === 'error') return { ...next, error: event.content.message }

  const ticker = eventTicker(event)
  if (!ticker) return next // not a comparison step: ignore
  const isNew = !state.tickers.includes(ticker)
  // A new company starting means the previous ones are complete
  const perTicker = isNew ? finishAll(state.perTicker, 'done') : { ...state.perTicker }
  perTicker[ticker] = runReducer(perTicker[ticker] ?? initialRunState, { type: 'event', event })
  return { ...next, tickers: isNew ? [...state.tickers, ticker] : state.tickers, perTicker }
}

/** What is happening now in a live comparison (null once finished). */
export function currentCompareActivity(state: CompareViewState, expectedTickers: string[]): string | null {
  if (state.finished || state.error || state.comparison) return null
  const active = state.tickers[state.tickers.length - 1]
  if (!active) return `Starting with ${expectedTickers[0] ?? 'the first company'}: fetching market data and the latest 10-K…`
  const activity = currentActivity(state.perTicker[active])
  const allResearched = expectedTickers.length > 0 && expectedTickers.every((t) => state.perTicker[t]?.auditor)
  if (allResearched) return `Comparator is weighing ${expectedTickers.join(' vs ')}…`
  if (activity === 'Finishing…' || activity === null) {
    const nextTicker = expectedTickers[expectedTickers.indexOf(active) + 1]
    return nextTicker ? `Moving on to ${nextTicker}…` : null
  }
  return `${active}: ${activity}`
}
