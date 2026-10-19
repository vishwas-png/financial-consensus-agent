// Draws a comparison run: the Comparator's verdict, then one tab per company showing that
// company's full research (the same RunView a single run uses). Purely presentational.

import { useState } from 'react'
import type { ComparisonOutput } from '../api/types'
import { currentCompareActivity, type CompareViewState } from './compareReducer'
import { PipelineProgress } from './PipelineProgress'
import { RunView } from './RunView'

type Excerpts = Record<string, { section: string; text: string }>

interface CompareRunViewProps {
  /** The tickers being compared, from the run (known before any event arrives). */
  tickers: string[]
  state: CompareViewState
  excerpts?: Excerpts
  /** When the comparison started (server time), for the pipeline's timings. */
  startedAt?: string
}

/** When this company's research began: the run start, or when the previous company finished. */
function companyStart(state: CompareViewState, ticker: string, runStart?: string): string | undefined {
  const index = state.tickers.indexOf(ticker)
  if (index <= 0) return runStart
  const previous = state.perTicker[state.tickers[index - 1]]
  return previous?.stepTimes[previous.stepTimes.length - 1]?.at
}

export function CompareRunView({ tickers, state, excerpts = {}, startedAt }: CompareRunViewProps) {
  // null = follow the company currently being researched; a click pins a tab
  const [pinned, setPinned] = useState<string | null>(null)
  const live = state.tickers[state.tickers.length - 1] ?? tickers[0]
  const selected = pinned ?? live
  const activity = currentCompareActivity(state, tickers)

  return (
    <div className="compare-view">
      {state.comparison && <ComparisonCard comparison={state.comparison} />}
      {state.error && (
        <section className="card card-error" role="alert">
          <h3>Comparison failed</h3>
          <p>{state.error}</p>
        </section>
      )}
      {activity && state.perTicker[live] && activity.startsWith(`${live}:`) ? (
        // A company is being researched: show its pipeline, like a single run
        <PipelineProgress
          state={state.perTicker[live]}
          startedAt={companyStart(state, live, startedAt)}
          title={`${live} · company ${tickers.indexOf(live) + 1} of ${tickers.length}`}
          activity={activity}
        />
      ) : (
        activity && (
          <p className="activity" aria-live="polite">
            <span className="spinner" aria-hidden="true" /> {activity}
          </p>
        )
      )}
      <div className="tabs" role="tablist" aria-label="Companies">
        {tickers.map((ticker) => (
          <button
            key={ticker}
            type="button"
            role="tab"
            id={`tab-${ticker}`}
            aria-selected={selected === ticker}
            aria-controls={`panel-${ticker}`}
            className={`tab${selected === ticker ? ' tab-active' : ''}`}
            onClick={() => setPinned(ticker)}
            disabled={!state.perTicker[ticker]}
          >
            {ticker}
          </button>
        ))}
      </div>
      {selected && state.perTicker[selected] && (
        <div role="tabpanel" id={`panel-${selected}`} aria-labelledby={`tab-${selected}`}>
          <RunView ticker={selected} state={state.perTicker[selected]} excerpts={excerpts} showProgress={false} />
        </div>
      )}
    </div>
  )
}

const STANCE_CLASS = { bullish: 'badge-ok', neutral: '', bearish: 'badge-warn' } as const

function ComparisonCard({ comparison }: { comparison: ComparisonOutput }) {
  return (
    <article className="card card-comparison">
      <header>
        <h3>Comparison</h3>
        <span className={`badge badge-${comparison.confidence}`}>{comparison.confidence} confidence</span>
      </header>
      <p className="thesis">
        {comparison.preferred ? (
          <>
            Preferred on this evidence: <strong>{comparison.preferred}</strong>
          </>
        ) : (
          'No clear preference on this evidence.'
        )}
      </p>
      <p>{comparison.preference_reasoning}</p>
      <ul className="per-ticker">
        {comparison.per_ticker.map((entry) => (
          <li key={entry.ticker}>
            <span className="ticker">{entry.ticker}</span>{' '}
            <span className={`badge ${STANCE_CLASS[entry.stance]}`}>{entry.stance}</span> {entry.summary}
          </li>
        ))}
      </ul>
      <h4>Key differences</h4>
      <ul>
        {comparison.key_differences.map((difference) => (
          <li key={difference}>{difference}</li>
        ))}
      </ul>
      {comparison.filing_citations.length > 0 && (
        <p className="muted">10-K citations: {comparison.filing_citations.join(', ')}</p>
      )}
      <p className="muted">
        Written by an AI agent from the research in the tabs below. Each company's thesis was fact-checked by the
        Auditor; this comparison itself is not.
      </p>
    </article>
  )
}
