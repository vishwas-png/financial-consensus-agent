// Holds the signed-in user's Google ID token for the whole app.
// - restores a still-valid token from sessionStorage on page load
// - signs the user out automatically when the token expires (Google ID tokens last ~1 hour)

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AuthContext, type AuthState } from './context'
import { clearToken, decodeToken, isExpired, loadToken, saveToken } from './token'

function restoreToken(): string | null {
  const stored = loadToken()
  if (!stored) return null
  const profile = decodeToken(stored)
  if (!profile || isExpired(profile)) {
    clearToken()
    return null
  }
  return stored
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(restoreToken)
  const user = useMemo(() => (token ? decodeToken(token) : null), [token])

  const signIn = useCallback((newToken: string) => {
    const profile = decodeToken(newToken)
    if (!profile || isExpired(profile)) return // ignore unusable tokens
    saveToken(newToken)
    setToken(newToken)
  }, [])

  const signOut = useCallback(() => {
    clearToken()
    setToken(null)
    window.google?.accounts.id.disableAutoSelect() // don't silently sign back in
  }, [])

  // Auto sign-out when the token expires
  useEffect(() => {
    if (!user) return
    const msUntilExpiry = Math.max(0, (user.exp - 60) * 1000 - Date.now())
    const timer = setTimeout(signOut, msUntilExpiry)
    return () => clearTimeout(timer)
  }, [user, signOut])

  const value: AuthState = useMemo(() => ({ token, user, signIn, signOut }), [token, user, signIn, signOut])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
