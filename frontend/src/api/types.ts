// TypeScript mirrors of the backend's JSON (backend/src/core/schemas.py, api/routes_research.py,
// api/jobs.py). If the backend shape changes, the build fails here instead of the UI breaking silently.

export type RunStatus = 'pending' | 'running' | 'done' | 'failed'

export interface AnalystOutput {
  thesis: string
  supporting_data_points: string[]
  reasoning: string
  confidence: 'low' | 'medium' | 'high'
  filing_citations: string[]
}

export interface CriticFeedback {
  agrees: boolean
  objections: string[]
  missing_context: string[]
}

export interface AuditorVerdict {
  is_grounded: boolean
  unsupported_claims: string[]
  notes: string
}

// ---------- Run events (one per graph step; streamed over SSE and stored in the DB) ----------

export interface FetchDataContent {
  company: string | null
  price: number | null
  currency: string | null
  filing_excerpts: number
  filing_ids: string[]
}

interface EventBase {
  id: number
  timestamp: string
}

export type RunEvent = EventBase &
  (
    | { step: 'fetch_data'; content: FetchDataContent }
    | { step: 'analyst'; content: { analyst_output: AnalystOutput } }
    | { step: 'critic'; content: { critic_feedback: CriticFeedback } }
    | { step: 'consensus_check'; content: { iteration_count: number; forced_exit: boolean } }
    | { step: 'auditor'; content: { auditor_verdict: AuditorVerdict } }
    | { step: 'error'; content: { message: string; type?: string } }
  )

export type StepName = RunEvent['step']

// ---------- REST responses ----------

export interface FinalOutput {
  ticker: string
  outcome: 'consensus' | 'unresolved_disagreement'
  rounds: number
  max_iterations: number
  forced_exit: boolean
  analyst_output: AnalystOutput | null
  critic_feedback: CriticFeedback | null
  auditor_verdict: AuditorVerdict | null
  filing_chunks: string[] // "[AAPL-10K-2025-1A-007] (Item 1A - Risk Factors) text..."
  market_snapshot: Record<string, unknown>
}

export interface RunCreated {
  run_id: string
  ticker: string
  status: RunStatus
}

export interface RunDetail {
  id: string
  ticker: string
  status: RunStatus
  created_at: string
  final_output: FinalOutput | null
  events: RunEvent[]
}

export interface RunSummary {
  id: string
  ticker: string
  status: RunStatus
  created_at: string
  outcome: FinalOutput['outcome'] | null
  thesis: string | null
}

export interface Me {
  id: string
  email: string
  quota: {
    runs_last_24h: number
    runs_per_day: number
    active_runs: number
    max_active_runs: number
    window_hours: number
  }
}

/** How a finished stream ended: the run's final status, or "timeout" if the server gave up. */
export type StreamEnd = 'done' | 'failed' | 'timeout'
