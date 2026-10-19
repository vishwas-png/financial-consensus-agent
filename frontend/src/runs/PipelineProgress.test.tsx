import { act, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent } from '../api/types'
import { FORCED_EXIT_EVENTS } from '../test/fixtures'
import { PipelineProgress } from './PipelineProgress'
import { initialRunState, runReducer } from './runReducer'

const stateFrom = (events: RunEvent[], end?: 'done' | 'failed') => {
  const state = events.reduce((s, event) => runReducer(s, { type: 'event', event }), initialRunState)
  return end ? runReducer(state, { type: 'end', status: end }) : state
}

afterEach(() => {
  vi.useRealTimers()
})

describe('PipelineProgress', () => {
  it('shows each step with its status and marks the current one', () => {
    render(<PipelineProgress state={stateFrom(FORCED_EXIT_EVENTS.slice(0, 2))} activity="Critic is reviewing round 1…" />)
    const steps = within(screen.getByRole('list')).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual([
      expect.stringMatching(/^✓Data.*done$/),
      expect.stringMatching(/^✓Analyst.*done$/),
      '●Criticworking…in progress',
      '○Consensuswaiting',
      '○Risk Officerwaiting',
      '○Auditorwaiting',
    ])
    expect(steps[2]).toHaveAttribute('aria-current', 'step')
    expect(screen.getByText(/Critic is reviewing round 1/)).toBeInTheDocument()
  })

  it('ticks an elapsed clock while live', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-10T08:01:05Z'))
    render(<PipelineProgress state={stateFrom(FORCED_EXIT_EVENTS.slice(0, 1))} startedAt="2026-10-10T08:00:00Z" />)
    expect(screen.getByText('Elapsed 1:05')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(screen.getByText('Elapsed 1:08')).toBeInTheDocument()
  })

  it('shows the total and per-step times once finished', () => {
    const timed = FORCED_EXIT_EVENTS.map((event, i) => ({
      ...event,
      timestamp: new Date(Date.UTC(2026, 9, 10, 8, 0, (i + 1) * 10)).toISOString(),
    }))
    render(<PipelineProgress state={stateFrom(timed, 'done')} startedAt="2026-10-10T08:00:00Z" />)
    expect(screen.getByText('Took 1:30')).toBeInTheDocument()
    const analyst = within(screen.getByRole('list')).getAllByRole('listitem')[1]
    expect(analyst).toHaveTextContent('20 s') // two rounds of 10 s
    expect(screen.queryByText(/Elapsed/)).not.toBeInTheDocument()
  })

  it('marks the step that failed', () => {
    const failed = stateFrom(
      [...FORCED_EXIT_EVENTS.slice(0, 2), { id: 50, timestamp: 't', step: 'error', content: { message: 'busy' } }],
      'failed',
    )
    render(<PipelineProgress state={failed} />)
    expect(within(screen.getByRole('list')).getAllByRole('listitem')[2]).toHaveTextContent('✗Criticfailed')
  })
})
