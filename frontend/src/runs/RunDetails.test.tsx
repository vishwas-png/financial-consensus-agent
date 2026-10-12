import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { METRICS } from '../test/fixtures'
import { RunDetails } from './RunDetails'

describe('RunDetails', () => {
  it('is collapsed until opened, then shows cost and time per step', async () => {
    render(<RunDetails metrics={METRICS} />)
    expect(screen.getByText('Total time')).not.toBeVisible()

    await userEvent.click(screen.getByText('Run details'))
    expect(screen.getByText('95.0 s')).toBeVisible()
    expect(screen.getByText(/^4 \(2 retried/)).toBeInTheDocument()
    expect(screen.getByText(/13,500 \(12,000 in, 1,500 out\)/)).toBeInTheDocument()
    expect(screen.getByText(/indexed during this run: 139 chunks in 28.9 s \(3 embedding calls\)/)).toBeInTheDocument()

    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual([
      'Market data + 10-K31.2 s', 'Analyst22.5 s', 'Critic18.0 s', 'Consensus check0.0 s', 'Auditor23.3 s',
    ])
  })

  it('says when the 10-K was already indexed and shows unknown step names as-is', async () => {
    render(<RunDetails metrics={{ ...METRICS, filing_ingest: null, llm_retries: 0, steps: [{ step: 'new_step', seconds: 1 }] }} />)
    await userEvent.click(screen.getByText('Run details'))
    expect(screen.getByText('already indexed (or not available)')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument() // no "(retried)" note
    expect(screen.getByText('new_step')).toBeInTheDocument()
  })
})
