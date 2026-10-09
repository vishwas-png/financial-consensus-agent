// /runs/:runId: loads the run, then streams its events live (or replays them if it already
// finished: the stream endpoint replays from the start), then loads the final output so
// citation chips can show the 10-K excerpt text.

import { useEffect, useReducer, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { streamRun } from '../api/sse'
import type { RunDetail } from '../api/types'
import { useApi } from '../api/useApi'
import { useAuth } from '../auth/context'
import { API_URL } from '../config'
import { initialRunState, parseExcerpts, runReducer } from '../runs/runReducer'
import { RunView } from '../runs/RunView'

export function RunPage() {
  const { runId = '' } = useParams()
  const api = useApi()
  const { token } = useAuth()
  const [state, dispatch] = useReducer(runReducer, initialRunState)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    dispatch({ type: 'reset' })

    async function load() {
      try {
        setDetail(await api.getRun(runId)) // also confirms the run exists and is ours
        const end = await streamRun({
          baseUrl: API_URL,
          runId,
          getToken: () => token,
          onEvent: (event) => dispatch({ type: 'event', event }),
          signal: controller.signal,
        })
        dispatch({ type: 'end', status: end })
        if (!controller.signal.aborted) setDetail(await api.getRun(runId)) // final output + excerpts
      } catch (e) {
        if (controller.signal.aborted) return // navigated away
        if (e instanceof ApiError && e.status === 404) setProblem('Run not found.')
        else setProblem(e instanceof Error ? e.message : 'Something went wrong.')
      }
    }

    load()
    return () => controller.abort()
  }, [api, runId, token])

  if (problem) {
    return (
      <main>
        <p className="error" role="alert">
          {problem}
        </p>
        <Link to="/">Start a new research run</Link>
      </main>
    )
  }

  const excerpts = parseExcerpts(detail?.final_output?.filing_chunks ?? [])
  return (
    <main>
      <h1>Research: {detail?.ticker ?? '…'}</h1>
      <RunView ticker={detail?.ticker ?? ''} state={state} excerpts={excerpts} />
      {state.finished && (
        <p>
          <Link to="/">Start another run</Link> · <Link to="/history">History</Link>
        </p>
      )}
    </main>
  )
}
