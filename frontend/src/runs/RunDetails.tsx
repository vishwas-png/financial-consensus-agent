// "Run details": what a finished run cost and where its time went (backend observability).
// Collapsed by default; purely presentational.

import type { RunMetrics } from '../api/types'

const STEP_LABELS: Record<string, string> = {
  fetch_data: 'Market data + 10-K',
  analyst: 'Analyst',
  critic: 'Critic',
  consensus_check: 'Consensus check',
  risk_officer: 'Risk Officer',
  auditor: 'Auditor',
  comparator: 'Comparator',
}

function seconds(value: number | null | undefined): string {
  return value == null ? 'n/a' : `${value.toFixed(1)} s`
}

export function RunDetails({ metrics }: { metrics: RunMetrics }) {
  const tokens = metrics.prompt_tokens + metrics.completion_tokens
  const ingest = metrics.filing_ingest
  return (
    <details className="card run-details">
      <summary>Run details</summary>
      <dl>
        <dt>Total time</dt>
        <dd>{seconds(metrics.total_seconds)}</dd>
        <dt>LLM calls</dt>
        <dd>
          {metrics.llm_calls}
          {metrics.llm_retries > 0 && ` (${metrics.llm_retries} retried after rate limits or errors)`}
        </dd>
        <dt>Tokens</dt>
        <dd>
          {tokens.toLocaleString('en-US')} ({metrics.prompt_tokens.toLocaleString('en-US')} in,{' '}
          {metrics.completion_tokens.toLocaleString('en-US')} out)
        </dd>
        <dt>10-K</dt>
        <dd>
          {ingest
            ? `indexed during this run: ${ingest.chunks} chunks in ${seconds(ingest.total_seconds)} (${metrics.embedding_calls} embedding calls)`
            : 'already indexed (or not available)'}
        </dd>
      </dl>
      <table>
        <caption>Time per step</caption>
        <thead>
          <tr>
            <th scope="col">Step</th>
            <th scope="col">Time</th>
          </tr>
        </thead>
        <tbody>
          {metrics.steps.map((step, index) => (
            <tr key={index}>
              <td>
                {STEP_LABELS[step.step] ?? step.step}
                {step.ticker ? ` (${step.ticker})` : ''}
              </td>
              <td>{seconds(step.seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
