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

export interface RiskAssessment {
  key_risks: { risk: string; severity: 'low' | 'medium' | 'high'; citation: string | null }[]
  what_would_change_the_thesis: string[]
  overall_risk: 'low' | 'medium' | 'high'
}

export interface DataPointCheck {
  data_point: string
  field: string | null
  claimed: number | null
  actual: number | null
  status: 'match' | 'mismatch' | 'unknown_field' | 'not_checkable'
}

export interface DataCheck {
  checks: DataPointCheck[]
  matched: number
  mismatched: number
  unchecked: number
}

export interface ComparisonOutput {
  per_ticker: { ticker: string; summary: string; stance: 'bullish' | 'neutral' | 'bearish' }[]
  key_differences: string[]
  preferred: string | null
  preference_reasoning: string
  confidence: 'low' | 'medium' | 'high'
  filing_citations: string[]
}

// ---------- Run events (one per graph step; streamed over SSE and stored in the DB) ----------

export interface FetchDataContent {
  company: string | null
  price: number | null
  currency: string | null
  filing_excerpts: number
  filing_ids: string[]
  /** Why there are (or aren't) excerpts. Missing on runs from before Stage 9. */
  filing_status?: 'found' | 'empty' | 'unavailable' | 'timeout' | null
}

interface EventBase {
  id: number
  timestamp: string
}

/** In a comparison run, every per-company step's content also says which company it is about. */
type Tagged<T> = T & { ticker?: string }

export type RunEvent = EventBase &
  (
    | { step: 'fetch_data'; content: Tagged<FetchDataContent> }
    | { step: 'analyst'; content: Tagged<{ analyst_output: AnalystOutput }> }
    | { step: 'critic'; content: Tagged<{ critic_feedback: CriticFeedback }> }
    | { step: 'consensus_check'; content: Tagged<{ iteration_count: number; forced_exit: boolean }> }
    | { step: 'risk_officer'; content: Tagged<{ risk_assessment: RiskAssessment }> }
    | { step: 'auditor'; content: Tagged<{ auditor_verdict: AuditorVerdict; data_check?: DataCheck | null }> }
    | { step: 'comparator'; content: { comparison: ComparisonOutput } }
    | { step: 'error'; content: { message: string; type?: string } }
  )

/** Comparison runs tag each per-company step with its ticker (content.ticker). */
export function eventTicker(event: RunEvent): string | null {
  return 'ticker' in event.content ? (event.content.ticker ?? null) : null
}

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
  risk_assessment?: RiskAssessment | null
  data_check?: DataCheck | null
  filing_chunks: string[] // "[AAPL-10K-2025-1A-007] (Item 1A - Risk Factors) text..."
  market_snapshot: Record<string, unknown>
}

export interface RunCreated {
  run_id: string
  ticker: string
  status: RunStatus
}

/** final_output of a comparison run (backend: api/jobs.py comparison_final_output). */
export interface CompareFinalOutput {
  kind: 'compare'
  tickers: string[]
  comparison: ComparisonOutput | null
  per_ticker: Record<string, FinalOutput>
  filing_chunks: string[]
}

export type RunKind = 'single' | 'compare'

export interface RunDetail {
  id: string
  ticker: string
  kind?: RunKind
  tickers?: string[] | null
  status: RunStatus
  created_at: string
  final_output: FinalOutput | CompareFinalOutput | null
  metrics?: RunMetrics | null
  events: RunEvent[]
}

/** Observability for one run (backend/src/observability.py). Missing on runs from before Stage 9. */
export interface RunMetrics {
  total_seconds: number | null
  llm_calls: number
  llm_retries: number
  prompt_tokens: number
  completion_tokens: number
  tokens_by_model: Record<string, number>
  embedding_calls: number
  embedded_texts: number
  filing_ingest: {
    ticker: string
    chunks: number
    total_seconds: number
    download_seconds: number
    parse_seconds: number
    chunk_seconds: number
    embed_seconds: number
  } | null
  steps: { step: string; seconds: number; ticker?: string }[]
}

export interface RunSummary {
  id: string
  ticker: string
  kind?: RunKind
  status: RunStatus
  created_at: string
  /** consensus / unresolved_disagreement, or for comparisons "prefers MSFT" / "no clear preference" */
  outcome: FinalOutput['outcome'] | string | null
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
