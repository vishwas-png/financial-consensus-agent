// The verdict scorecard: the thesis as the headline, then one tile per key fact. Each tile
// links to the card with the details (the debate section is opened if it was collapsed).

import type { MouseEvent } from 'react'
import type { Verdict } from './verdict'

function openTarget(event: MouseEvent<HTMLAnchorElement>) {
  const target = document.querySelector(event.currentTarget.hash)
  if (target instanceof HTMLDetailsElement) target.open = true
}

export function VerdictCard({ verdict }: { verdict: Verdict }) {
  return (
    <section className="card verdict" aria-labelledby="verdict-title">
      <h2 id="verdict-title" className="verdict-kicker">
        Verdict
      </h2>
      <p className="verdict-thesis">{verdict.analysis.thesis}</p>
      <ul className="verdict-facts">
        {verdict.facts.map((fact) => (
          <li key={fact.label}>
            <a href={fact.href} className={`fact fact-${fact.tone}`} onClick={openTarget}>
              <span className="fact-label">{fact.label}</span>
              <span className="fact-value">{fact.value}</span>
            </a>
          </li>
        ))}
      </ul>
      {verdict.topRisk && (
        <p className="verdict-risk">
          <span className="muted">Top risk:</span> {verdict.topRisk}
        </p>
      )}
      <p className="muted verdict-note">AI-generated research from public data. Not investment advice.</p>
    </section>
  )
}
