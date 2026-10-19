// Progress pipeline: Data -> Analyst -> Critic -> Consensus -> Risk Officer -> Auditor.
// Shows what is done (with time spent), what is running now, and what is still to come.

import { useEffect, useState } from 'react'
import { formatDuration, parseServerTime, pipelineSteps, totalSeconds, type StepStatus } from './pipeline'
import type { RunViewState } from './runReducer'

const ICON: Record<StepStatus, string> = { done: '✓', active: '●', pending: '○', failed: '✗', skipped: '–' }
const STATUS_TEXT: Record<StepStatus, string> = {
  done: 'done',
  active: 'in progress',
  pending: 'waiting',
  failed: 'failed',
  skipped: 'not part of this run',
}

interface PipelineProgressProps {
  state: RunViewState
  /** When the run (or this company's part of a comparison) started; enables timings. */
  startedAt?: string
  title?: string
  /** Live "what is happening now" text, announced to screen readers. */
  activity?: string | null
}

/** Seconds since `startedAt`, re-rendered every second while `running`. */
function useElapsed(startedAt: string | undefined, running: boolean): number | undefined {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  if (!startedAt) return undefined
  return Math.max(0, (now - parseServerTime(startedAt)) / 1000)
}

export function PipelineProgress({ state, startedAt, title = 'Progress', activity }: PipelineProgressProps) {
  const steps = pipelineSteps(state, startedAt)
  const live = !state.finished && !state.error
  const elapsed = useElapsed(startedAt, live)
  const total = totalSeconds(state, startedAt)
  const clock = live
    ? elapsed !== undefined && `Elapsed ${formatDuration(elapsed)}`
    : total !== undefined && `Took ${formatDuration(total)}`

  return (
    <section className="pipeline" aria-label={title}>
      <div className="pipeline-head">
        <strong>{title}</strong>
        {clock && <span className="muted">{clock}</span>}
      </div>
      <ol className="pipeline-steps">
        {steps.map((step) => (
          <li
            key={step.key}
            className={`step step-${step.status}`}
            aria-current={step.status === 'active' ? 'step' : undefined}
          >
            <span className="step-icon" aria-hidden="true">
              {ICON[step.status]}
            </span>
            <span className="step-label">{step.label}</span>
            <span className="step-meta">
              {step.status === 'active'
                ? (step.note ?? 'working…')
                : step.seconds !== undefined && step.status === 'done'
                  ? formatDuration(step.seconds)
                  : ''}
            </span>
            <span className="visually-hidden">{STATUS_TEXT[step.status]}</span>
          </li>
        ))}
      </ol>
      {activity && (
        <p className="activity" aria-live="polite">
          <span className="spinner" aria-hidden="true" /> {activity}
        </p>
      )}
    </section>
  )
}
