// Draws a run: data card, one section per debate round, the outcome banner, the auditor's
// verdict (or an error). Purely presentational: all logic lives in runReducer.ts.

import { useState } from 'react'
import type { AnalystOutput, AuditorVerdict, CriticFeedback, DataCheck, FetchDataContent, RiskAssessment } from '../api/types'
import { currentActivity, debateOutcome, type Round, type RunViewState } from './runReducer'

type Excerpts = Record<string, { section: string; text: string }>

interface RunViewProps {
  ticker: string
  state: RunViewState
  /** Filing excerpt texts by id; available once the run has finished. */
  excerpts?: Excerpts
  /** Off inside a comparison, which shows one activity line for the whole run. */
  showActivity?: boolean
}

export function RunView({ ticker, state, excerpts = {}, showActivity = true }: RunViewProps) {
  const activity = showActivity ? currentActivity(state) : null
  const outcome = debateOutcome(state)
  return (
    <div className="run-view">
      {state.data && <DataCard ticker={ticker} data={state.data} />}
      {state.rounds.map((round) => (
        <RoundSection key={round.number} round={round} excerpts={excerpts} />
      ))}
      {outcome && <OutcomeBanner kind={outcome.kind} rounds={outcome.rounds} />}
      {state.risk && <RiskCard assessment={state.risk} excerpts={excerpts} />}
      {state.auditor && <AuditorCard verdict={state.auditor} dataCheck={state.dataCheck} />}
      {state.error && (
        <section className="card card-error" role="alert">
          <h3>Run failed</h3>
          <p>{state.error}</p>
        </section>
      )}
      {state.finished === 'timeout' && (
        <p className="muted">The live stream stopped waiting for this run. Reload the page later to see the result.</p>
      )}
      {activity && (
        <p className="activity" aria-live="polite">
          <span className="spinner" aria-hidden="true" /> {activity}
        </p>
      )}
    </div>
  )
}

function DataCard({ ticker, data }: { ticker: string; data: FetchDataContent }) {
  const price = data.price != null ? `${data.price.toFixed(2)} ${data.currency ?? ''}`.trim() : 'n/a'
  return (
    <section className="card card-data">
      <h3>
        {data.company ?? ticker} <span className="ticker">{ticker}</span>
      </h3>
      <p>
        Price {price} · {data.filing_excerpts} excerpts from the latest 10-K
      </p>
      {data.filing_status && data.filing_status !== 'found' && (
        <p className="muted">{FILING_STATUS_NOTES[data.filing_status]}</p>
      )}
    </section>
  )
}

// Shown when a run has no 10-K evidence, so the reader knows the debate used market data only
const FILING_STATUS_NOTES: Record<'empty' | 'unavailable' | 'timeout', string> = {
  empty: 'No 10-K text was found for this company, so the agents used market data only.',
  unavailable: 'The 10-K could not be loaded right now, so the agents used market data only.',
  timeout: 'Indexing the 10-K took too long, so this run used market data only. The next run should have it.',
}

function RoundSection({ round, excerpts }: { round: Round; excerpts: Excerpts }) {
  return (
    <section className="round" aria-label={`Round ${round.number}`}>
      <h2>Round {round.number}</h2>
      {round.analyst && <AnalystCard output={round.analyst} excerpts={excerpts} />}
      {round.critic && <CriticCard feedback={round.critic} />}
    </section>
  )
}

function AnalystCard({ output, excerpts }: { output: AnalystOutput; excerpts: Excerpts }) {
  const [openCitation, setOpenCitation] = useState<string | null>(null)
  const excerpt = openCitation ? excerpts[openCitation] : undefined
  return (
    <article className="card card-analyst">
      <header>
        <h3>Analyst</h3>
        <span className={`badge badge-${output.confidence}`}>{output.confidence} confidence</span>
      </header>
      <p className="thesis">{output.thesis}</p>
      <ul>
        {output.supporting_data_points.map((point) => (
          <li key={point}>{point}</li>
        ))}
      </ul>
      <details>
        <summary>Reasoning</summary>
        <p>{output.reasoning}</p>
      </details>
      {output.filing_citations.length > 0 && (
        <div className="citations">
          <span className="muted">10-K citations:</span>
          {output.filing_citations.map((id) => (
            <button
              key={id}
              type="button"
              className={`chip${openCitation === id ? ' chip-active' : ''}`}
              onClick={() => setOpenCitation(openCitation === id ? null : id)}
              aria-expanded={openCitation === id}
            >
              {id}
            </button>
          ))}
        </div>
      )}
      {openCitation && (
        <blockquote className="excerpt">
          {excerpt ? (
            <>
              <strong>{excerpt.section}</strong> {excerpt.text}
            </>
          ) : (
            'The excerpt text is shown once the run has finished.'
          )}
        </blockquote>
      )}
    </article>
  )
}

function CriticCard({ feedback }: { feedback: CriticFeedback }) {
  return (
    <article className="card card-critic">
      <header>
        <h3>Critic</h3>
        <span className={`badge ${feedback.agrees ? 'badge-ok' : 'badge-warn'}`}>
          {feedback.agrees ? '✓ Agrees' : '✗ Objects'}
        </span>
      </header>
      {feedback.objections.length > 0 && (
        <>
          <h4>Objections</h4>
          <ul>
            {feedback.objections.map((objection) => (
              <li key={objection}>{objection}</li>
            ))}
          </ul>
        </>
      )}
      {feedback.missing_context.length > 0 && (
        <>
          <h4>Missing context</h4>
          <ul>
            {feedback.missing_context.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
    </article>
  )
}

function OutcomeBanner({ kind, rounds }: { kind: 'consensus' | 'unresolved'; rounds: number }) {
  return kind === 'consensus' ? (
    <p className="banner banner-ok">Consensus reached in round {rounds}.</p>
  ) : (
    <p className="banner banner-warn">
      Unresolved disagreement: the round limit ({rounds}) was reached and the critic still objects. Treat the thesis
      with caution.
    </p>
  )
}

function RiskCard({ assessment, excerpts }: { assessment: RiskAssessment; excerpts: Excerpts }) {
  return (
    <article className="card card-risk">
      <header>
        <h3>Risk Officer</h3>
        <span className={`badge risk-${assessment.overall_risk}`}>{assessment.overall_risk} overall risk</span>
      </header>
      <ul>
        {assessment.key_risks.map((item) => (
          <li key={item.risk}>
            <span className={`badge risk-${item.severity}`}>{item.severity}</span> {item.risk}{' '}
            <span className="muted" title={item.citation ? excerpts[item.citation]?.text : undefined}>
              ({item.citation ?? 'market data'})
            </span>
          </li>
        ))}
      </ul>
      <h4>What would change the thesis</h4>
      <ul>
        {assessment.what_would_change_the_thesis.map((condition) => (
          <li key={condition}>{condition}</li>
        ))}
      </ul>
    </article>
  )
}

function formatNumber(value: number | null): string {
  if (value == null) return 'n/a'
  return Math.abs(value) >= 1e6 ? value.toExponential(3) : String(Number(value.toPrecision(6)))
}

/** The Data Checker's result: plain code comparing each "field = value" with the market data. */
function DataCheckSummary({ check }: { check: DataCheck }) {
  const mismatches = check.checks.filter((c) => c.status === 'mismatch')
  return (
    <div className="data-check">
      <p>
        <strong>Data check (code, no AI):</strong> {check.matched} of {check.checks.length} data points match the
        market data
        {check.mismatched > 0 && `, ${check.mismatched} do not`}
        {check.unchecked > 0 && `, ${check.unchecked} could not be checked`}.
      </p>
      {mismatches.length > 0 && (
        <ul>
          {mismatches.map((c) => (
            <li key={c.data_point}>
              {c.data_point}: the data has {c.field} = {formatNumber(c.actual)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AuditorCard({ verdict, dataCheck }: { verdict: AuditorVerdict; dataCheck?: DataCheck }) {
  return (
    <article className={`card card-auditor ${verdict.is_grounded ? 'card-ok' : 'card-warn'}`}>
      <header>
        <h3>Auditor (fact check)</h3>
        <span className={`badge ${verdict.is_grounded ? 'badge-ok' : 'badge-warn'}`}>
          {verdict.is_grounded ? '✓ Grounded in the evidence' : '✗ Unsupported claims found'}
        </span>
      </header>
      {verdict.unsupported_claims.length > 0 && (
        <ul>
          {verdict.unsupported_claims.map((claim) => (
            <li key={claim}>{claim}</li>
          ))}
        </ul>
      )}
      <p className="muted">{verdict.notes}</p>
      {dataCheck && dataCheck.checks.length > 0 && <DataCheckSummary check={dataCheck} />}
    </article>
  )
}
