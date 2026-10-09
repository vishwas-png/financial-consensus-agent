// Loads Google Identity Services (GIS), the official "Sign in with Google" script.
// We use it directly instead of a wrapper library: it's a few calls and easy to explain.
// Docs: https://developers.google.com/identity/gsi/web/reference/js-reference

export interface CredentialResponse {
  credential: string // the Google ID token (JWT)
}

export interface GoogleIdentity {
  initialize(config: {
    client_id: string
    callback: (response: CredentialResponse) => void
    auto_select?: boolean
    cancel_on_tap_outside?: boolean
  }): void
  renderButton(parent: HTMLElement, options: Record<string, string | number>): void
  disableAutoSelect(): void
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleIdentity } }
  }
}

const SCRIPT_SRC = 'https://accounts.google.com/gsi/client'
let loading: Promise<GoogleIdentity> | null = null

/** Inject the GIS script once and resolve with google.accounts.id. */
export function loadGoogleIdentity(): Promise<GoogleIdentity> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id)
  if (loading) return loading
  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.defer = true
    script.onload = () => {
      if (window.google?.accounts?.id) resolve(window.google.accounts.id)
      else reject(new Error('Google sign-in loaded but is unavailable'))
    }
    script.onerror = () => {
      loading = null // allow a retry
      reject(new Error('Could not load Google sign-in. Check your connection or ad blocker.'))
    }
    document.head.appendChild(script)
  })
  return loading
}
