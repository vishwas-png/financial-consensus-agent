// Renders Google's official "Sign in with Google" button and passes the ID token to signIn().

import { useEffect, useRef, useState } from 'react'
import { GOOGLE_CLIENT_ID } from '../config'
import { useAuth } from './context'
import { loadGoogleIdentity, type GoogleIdentity } from './google'

interface Props {
  clientId?: string
  loadGoogle?: () => Promise<GoogleIdentity> // injectable for tests
}

export function GoogleSignInButton({ clientId = GOOGLE_CLIENT_ID, loadGoogle = loadGoogleIdentity }: Props) {
  const { signIn } = useAuth()
  const container = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!clientId) {
      setError('Google sign-in is not configured (set VITE_GOOGLE_CLIENT_ID in frontend/.env).')
      return
    }
    let cancelled = false
    loadGoogle()
      .then((google) => {
        if (cancelled || !container.current) return
        google.initialize({
          client_id: clientId,
          callback: (response) => signIn(response.credential),
          auto_select: false,
          cancel_on_tap_outside: true,
        })
        google.renderButton(container.current, { theme: 'outline', size: 'large', text: 'signin_with', shape: 'pill' })
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [clientId, loadGoogle, signIn])

  return (
    <div>
      <div ref={container} data-testid="google-button" />
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
