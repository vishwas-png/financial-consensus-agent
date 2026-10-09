import { createContext, useContext } from 'react'
import type { TokenProfile } from './token'

export interface AuthState {
  token: string | null
  user: TokenProfile | null
  /** Called with a fresh Google ID token after sign-in. */
  signIn: (token: string) => void
  signOut: () => void
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthProvider>')
  return auth
}
