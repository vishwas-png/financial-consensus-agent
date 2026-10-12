// Realistic run events, shaped exactly like the backend's SSE/REST payloads.
import type { AnalystOutput, AuditorVerdict, CriticFeedback, DataCheck, RiskAssessment, RunEvent, RunMetrics } from '../api/types'

const ts = '2026-10-10T08:00:00Z'

export const ANALYSIS_1: AnalystOutput = {
  thesis: 'Apple looks fairly valued: strong margins offset a premium multiple.',
  supporting_data_points: ['trailing_pe = 38.6', 'profit_margin = 27.6%'],
  reasoning: 'Margins and free cash flow are strong, but growth is moderate.',
  confidence: 'medium',
  filing_citations: ['AAPL-10K-2025-1A-023'],
}

export const ANALYSIS_2: AnalystOutput = {
  ...ANALYSIS_1,
  thesis: 'Apple is modestly overvalued given ~6% annual revenue growth.',
  confidence: 'low',
  filing_citations: ['AAPL-10K-2025-1A-023', 'AAPL-10K-2025-7-015'],
}

export const OBJECTS: CriticFeedback = {
  agrees: false,
  objections: ['Quarterly YoY growth (16.4%) was presented as annual.'],
  missing_context: ['Share buybacks of $89.3B'],
}

export const AGREES: CriticFeedback = { agrees: true, objections: [], missing_context: [] }

export const VERDICT_FLAGGED: AuditorVerdict = {
  is_grounded: false,
  unsupported_claims: ['"16.4% annual growth" -- revenue_growth is quarterly YoY'],
  notes: 'One period misread.',
}

export const RISK: RiskAssessment = {
  key_risks: [
    { risk: 'Single-source suppliers could disrupt iPhone supply.', severity: 'high', citation: 'AAPL-10K-2025-1A-023' },
    { risk: 'A trailing P/E near 39 leaves little room for a growth miss.', severity: 'medium', citation: null },
  ],
  what_would_change_the_thesis: ['revenue_growth turns negative'],
  overall_risk: 'medium',
}

export const DATA_CHECK: DataCheck = {
  checks: [
    { data_point: 'trailing_pe = 39.0', field: 'trailing_pe', claimed: 39, actual: 39.04, status: 'match' },
    { data_point: 'revenue_growth = 16.4%', field: 'revenue_growth', claimed: 16.4, actual: 0.079, status: 'mismatch' },
    { data_point: 'Strong brand', field: null, claimed: null, actual: null, status: 'not_checkable' },
  ],
  matched: 1,
  mismatched: 1,
  unchecked: 1,
}

export const FETCH: RunEvent = {
  id: 1, timestamp: ts, step: 'fetch_data',
  content: { company: 'Apple Inc.', price: 336.9, currency: 'USD', filing_excerpts: 8, filing_ids: ['AAPL-10K-2025-1A-023'] },
}

/** Two rounds, the critic never agrees -> forced exit -> auditor. */
export const FORCED_EXIT_EVENTS: RunEvent[] = [
  FETCH,
  { id: 2, timestamp: ts, step: 'analyst', content: { analyst_output: ANALYSIS_1 } },
  { id: 3, timestamp: ts, step: 'critic', content: { critic_feedback: OBJECTS } },
  { id: 4, timestamp: ts, step: 'consensus_check', content: { iteration_count: 1, forced_exit: false } },
  { id: 5, timestamp: ts, step: 'analyst', content: { analyst_output: ANALYSIS_2 } },
  { id: 6, timestamp: ts, step: 'critic', content: { critic_feedback: OBJECTS } },
  { id: 7, timestamp: ts, step: 'consensus_check', content: { iteration_count: 2, forced_exit: true } },
  { id: 8, timestamp: ts, step: 'risk_officer', content: { risk_assessment: RISK } },
  { id: 9, timestamp: ts, step: 'auditor', content: { auditor_verdict: VERDICT_FLAGGED, data_check: DATA_CHECK } },
]

/** One round, the critic agrees -> consensus -> auditor. Shaped like a run from before Stage 9
 *  (no risk_officer step, no data_check), so older runs in history keep rendering. */
export const CONSENSUS_EVENTS: RunEvent[] = [
  FETCH,
  { id: 2, timestamp: ts, step: 'analyst', content: { analyst_output: ANALYSIS_1 } },
  { id: 3, timestamp: ts, step: 'critic', content: { critic_feedback: AGREES } },
  { id: 4, timestamp: ts, step: 'consensus_check', content: { iteration_count: 1, forced_exit: false } },
  { id: 5, timestamp: ts, step: 'auditor', content: { auditor_verdict: { is_grounded: true, unsupported_claims: [], notes: 'All good.' } } },
]

export const EXCERPT_CHUNKS = [
  '[AAPL-10K-2025-1A-023] (Item 1A - Risk Factors) The Company obtains certain components from single or limited sources.',
  '[AAPL-10K-2025-7-015] (Item 7 - Management\'s Discussion and Analysis) During 2025, the Company repurchased $89.3 billion of its common stock.',
]

/** Metrics as saved by the backend for a finished run (Stage 9). */
export const METRICS: RunMetrics = {
  total_seconds: 95.04,
  llm_calls: 4,
  llm_retries: 2,
  prompt_tokens: 12000,
  completion_tokens: 1500,
  tokens_by_model: { 'openai/gpt-oss-120b': 13500 },
  embedding_calls: 3,
  embedded_texts: 230,
  filing_ingest: { ticker: 'AAPL', chunks: 139, total_seconds: 28.9, download_seconds: 1, parse_seconds: 0.8, chunk_seconds: 7.6, embed_seconds: 19.5 },
  steps: [
    { step: 'fetch_data', seconds: 31.2 },
    { step: 'analyst', seconds: 22.5 },
    { step: 'critic', seconds: 18.0 },
    { step: 'consensus_check', seconds: 0.01 },
    { step: 'auditor', seconds: 23.3 },
  ],
}
