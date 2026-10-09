// Realistic run events, shaped exactly like the backend's SSE/REST payloads.
import type { AnalystOutput, AuditorVerdict, CriticFeedback, RunEvent } from '../api/types'

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
  { id: 8, timestamp: ts, step: 'auditor', content: { auditor_verdict: VERDICT_FLAGGED } },
]

/** One round, the critic agrees -> consensus -> auditor. */
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
