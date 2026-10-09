// Helpers for the Google ID token (a JWT: header.payload.signature).
//
// The browser only READS the payload to show the user's name and to know when the token
// expires. It never trusts it for security: the backend verifies the signature on every request.
//
// Storage: sessionStorage (this tab only, cleared when the tab closes), not localStorage,
// so a stolen-token window is as small as possible.

export interface TokenProfile {
  sub: string
  email: string
  name?: string
  picture?: string
  exp: number // seconds since epoch
}

const STORAGE_KEY = 'fca.google_id_token'
const EXPIRY_SKEW_SECONDS = 60 // treat the token as expired a minute early

/** Decode the JWT payload (base64url JSON). Returns null if the token is malformed. */
export function decodeToken(token: string): TokenProfile | null {
  const parts = token.split('.')
  if (parts.length !== 3) return null
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
    const json = decodeURIComponent(
      Array.from(atob(padded), (c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join(''),
    )
    const payload = JSON.parse(json)
    if (typeof payload.sub !== 'string' || typeof payload.exp !== 'number') return null
    return payload as TokenProfile
  } catch {
    return null
  }
}

export function isExpired(profile: TokenProfile, nowMs: number = Date.now()): boolean {
  return profile.exp - EXPIRY_SKEW_SECONDS <= nowMs / 1000
}

// sessionStorage can throw (privacy modes, disabled storage); the app must still work without it.
export function saveToken(token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, token)
  } catch {
    // ignore: the user just has to sign in again after a reload
  }
}

export function loadToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
