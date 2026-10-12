import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { RunSummary } from '../api/types'
import { useApi } from '../api/useApi'

const OUTCOME_LABEL: Record<string, string> = {
  consensus: 'Consensus',
  unresolved_disagreement: 'Unresolved disagreement',
}

export function HistoryPage() {
  const api = useApi()
  const [runs, setRuns] = useState<RunSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getHistory()
      .then(setRuns)
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Could not load your history.'))
  }, [api])

  return (
    <main>
      <h1>Your research history</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {runs === null && !error && <p className="muted">Loading…</p>}
      {runs?.length === 0 && (
        <p>
          No runs yet. <Link to="/">Start your first research run.</Link>
        </p>
      )}
      {runs && runs.length > 0 && (
        <ul className="history">
          {runs.map((run) => (
            <li key={run.id}>
              <Link to={`/runs/${run.id}`} className="history-row">
                {/* one grid cell: the row is a 4-column grid (name, status, outcome, time) */}
                <span>
                  <span className="ticker">{run.ticker}</span>
                  {run.kind === 'compare' && <span className="badge badge-kind">comparison</span>}
                </span>
                <span className={`badge badge-status-${run.status}`}>{run.status}</span>
                {/* comparisons arrive as plain text ("prefers MSFT"), single runs as a code */}
                {run.outcome && <span className="muted">{OUTCOME_LABEL[run.outcome] ?? run.outcome}</span>}
                <time dateTime={run.created_at}>{new Date(run.created_at).toLocaleString()}</time>
                {run.thesis && <span className="history-thesis">{run.thesis}</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}
