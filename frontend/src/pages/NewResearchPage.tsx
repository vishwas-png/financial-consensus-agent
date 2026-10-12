import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { Me } from '../api/types'
import { useApi } from '../api/useApi'

const EXAMPLES = ['How is Apple doing?', 'Give me a read on Microsoft', 'NVDA', 'Berkshire Hathaway class B']
const COMPARE_EXAMPLES = ['Apple vs Microsoft', 'NVDA vs AMD', 'Coca-Cola vs PepsiCo', 'Visa, Mastercard and PayPal']

type Mode = 'single' | 'compare'

export function NewResearchPage() {
  const api = useApi()
  const navigate = useNavigate()
  const [mode, setMode] = useState<Mode>('single')
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
      const created =
        mode === 'compare' ? await api.startComparison(query.trim()) : await api.startResearch(query.trim(), rounds)
      navigate(`/runs/${created.run_id}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not reach the server. Is the backend running?')
      setSubmitting(false)
    }
  }

  return (
    <main>
      <h1>New research</h1>
      <div className="tabs" role="tablist" aria-label="Research type">
        {(['single', 'compare'] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            className={`tab${mode === value ? ' tab-active' : ''}`}
            onClick={() => {
              setMode(value)
              setError(null)
            }}
          >
            {value === 'single' ? 'One company' : 'Compare companies'}
          </button>
        ))}
      </div>
      <form className="research-form" onSubmit={submit}>
        <label htmlFor="query">{mode === 'compare' ? 'Companies to compare (2 or 3)' : 'Company or question'}</label>
        <input
          id="query"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={mode === 'compare' ? 'e.g. Apple vs Microsoft' : 'e.g. How is Apple doing?'}
          maxLength={200}
          autoFocus
        />
        <div className="examples">
          {(mode === 'compare' ? COMPARE_EXAMPLES : EXAMPLES).map((example) => (
            <button key={example} type="button" className="chip" onClick={() => setQuery(example)}>
              {example}
            </button>
          ))}
        </div>
        {mode === 'single' ? (
          <>
            <label htmlFor="rounds">Maximum debate rounds</label>
            <select id="rounds" value={rounds} onChange={(e) => setRounds(Number(e.target.value))}>
              <option value={1}>1 (fastest)</option>
              <option value={2}>2</option>
              <option value={3}>3 (most thorough)</option>
            </select>
          </>
        ) : (
          <p className="muted">
            Each company gets one debate round, then a Comparator weighs them side by side. Uses one daily run per
            company and takes about 3–4 minutes per company.
          </p>
        )}
        <button type="submit" className="primary" disabled={submitting || !query.trim()}>
          {submitting ? 'Starting…' : mode === 'compare' ? 'Start comparison' : 'Start research'}
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
