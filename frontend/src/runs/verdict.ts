// The verdict scorecard: the run's answer in one place, derived from the same RunViewState
// the detailed cards use. Pure, so it is easy to test and identical live or replayed.

import type { AnalystOutput } from '../api/types'
import { debateOutcome, type RunViewState } from './runReducer'

export type Tone = 'ok' | 'warn' | 'neutral'

export interface VerdictFact {
  label: string
  value: string
  tone: Tone
  /** In-page anchor of the card with the details. */
  href: '#debate' | '#auditor' | '#risk'
}

export interface Verdict {
  analysis: AnalystOutput
  facts: VerdictFact[]
  topRisk?: string
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

/** The verdict once the run has a final analysis and an audit; null before that (or if it failed). */
export function buildVerdict(state: RunViewState): Verdict | null {
  const analysis = state.rounds[state.rounds.length - 1]?.analyst
  const outcome = debateOutcome(state)
  if (!analysis || !state.auditor || !outcome || state.error) return null

  const facts: VerdictFact[] = [
    outcome.kind === 'consensus'
      ? { label: 'Debate', value: `Consensus in round ${outcome.rounds}`, tone: 'ok', href: '#debate' }
      : { label: 'Debate', value: `Unresolved after ${plural(outcome.rounds, 'round')}`, tone: 'warn', href: '#debate' },
    {
      label: 'Confidence',
      value: analysis.confidence,
      tone: analysis.confidence === 'high' ? 'ok' : analysis.confidence === 'low' ? 'warn' : 'neutral',
      href: '#debate',
    },
    state.auditor.is_grounded
      ? { label: 'Fact check', value: 'Grounded in the evidence', tone: 'ok', href: '#auditor' }
      : {
          label: 'Fact check',
          value: `${plural(state.auditor.unsupported_claims.length, 'unsupported claim')}`,
          tone: 'warn',
          href: '#auditor',
        },
  ]

  if (state.dataCheck && state.dataCheck.checks.length > 0) {
    const checked = state.dataCheck.matched + state.dataCheck.mismatched
    facts.push(
      checked === 0
        ? { label: 'Numbers', value: 'None checkable', tone: 'neutral', href: '#auditor' }
        : {
            label: 'Numbers',
            value: `${state.dataCheck.matched}/${checked} match the data`,
            tone: state.dataCheck.mismatched === 0 ? 'ok' : 'warn',
            href: '#auditor',
          },
    )
  }
  if (state.risk) {
    const level = state.risk.overall_risk
    facts.push({
      label: 'Risk',
      value: `${level} overall`,
      tone: level === 'low' ? 'ok' : level === 'high' ? 'warn' : 'neutral',
      href: '#risk',
    })
  }
  return { analysis, facts, topRisk: state.risk?.key_risks[0]?.risk }
}
