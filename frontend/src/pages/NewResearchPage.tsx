import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { Me } from '../api/types'
import { useApi } from '../api/useApi'

const EXAMPLES = ['How is Apple doing?', 'Give me a read on Microsoft', 'NVDA', 'Berkshire Hathaway class B']

export function NewResearchPage() {
  const api = useApi()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [rounds, setRounds] = useState(2) // fewer rounds = faster on the Groq free tier
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [me, setMe] = useState<Me | null>(null)

  useEffect(() => {
    api.getMe().then(setMe).catch(() => setMe(null)) // quota display is optional
  }, [api])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!query.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await api.startResearch(query.trim(), rounds)
      navigate(`/runs/${created.run_id}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not reach the server. Is the backend running?')
      setSubmitting(false)
    }
  }

  return (
    <main>
      <h1>New research</h1>
      <form className="research-form" onSubmit={submit}>
        <label htmlFor="query">Company or question</label>
        <input
          id="query"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. How is Apple doing?"
          maxLength={200}
          autoFocus
        />
        <div className="examples">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" className="chip" onClick={() => setQuery(example)}>
              {example}
            </button>
          ))}
        </div>
        <label htmlFor="rounds">Maximum debate rounds</label>
        <select id="rounds" value={rounds} onChange={(e) => setRounds(Number(e.target.value))}>
          <option value={1}>1 (fastest)</option>
          <option value={2}>2</option>
          <option value={3}>3 (most thorough)</option>
        </select>
        <button type="submit" className="primary" disabled={submitting || !query.trim()}>
          {submitting ? 'Starting…' : 'Start research'}
        </button>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </form>
      {me && (
        <p className="muted">
          {me.quota.runs_last_24h}/{me.quota.runs_per_day} runs used in the last {me.quota.window_hours} hours. A run
          takes 1–4 minutes.
        </p>
      )}
    </main>
  )
}
