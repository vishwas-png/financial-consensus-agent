import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeToken } from '../test/tokens'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './context'
import { GoogleSignInButton } from './GoogleSignInButton'
import type { CredentialResponse, GoogleIdentity } from './google'
import { clearToken, decodeToken, isExpired, loadToken, saveToken } from './token'

beforeEach(() => {
  sessionStorage.clear()
})

// ---------- token helpers ----------

describe('token helpers', () => {
  it('decodes the payload, including UTF-8 names', () => {
    const profile = decodeToken(makeToken())
    expect(profile).toMatchObject({ sub: 'alice-sub', email: 'alice@example.com', name: 'Alice Ändersson' })
  })

  it.each(['', 'not-a-jwt', 'a.b', 'a.!!!.c', makeToken({ sub: 123 })])('rejects malformed token %#', (token) => {
    expect(decodeToken(token)).toBeNull()
  })

  it('treats tokens as expired a minute early', () => {
    const now = 1_000_000_000_000
    expect(isExpired({ sub: 's', email: 'e', exp: now / 1000 + 30 }, now)).toBe(true)
    expect(isExpired({ sub: 's', email: 'e', exp: now / 1000 + 120 }, now)).toBe(false)
  })

  it('stores the token in sessionStorage only', () => {
    saveToken('t1')
    expect(loadToken()).toBe('t1')
    expect(localStorage.length).toBe(0)
    clearToken()
    expect(loadToken()).toBeNull()
  })
})

// ---------- AuthProvider ----------

function Probe() {
  const { user, signIn, signOut } = useAuth()
  return (
    <div>
      <span data-testid="user">{user ? user.email : 'signed-out'}</span>
      <button onClick={() => signIn(makeToken({ email: 'new@example.com' }))}>sign in</button>
      <button onClick={() => signIn(makeToken({ exp: 1 }))}>sign in expired</button>
      <button onClick={signOut}>sign out</button>
    </div>
  )
}

describe('AuthProvider', () => {
  it('restores a valid token from sessionStorage', () => {
    saveToken(makeToken())
    render(<AuthProvider><Probe /></AuthProvider>)
    expect(screen.getByTestId('user')).toHaveTextContent('alice@example.com')
  })

  it('drops an expired stored token', () => {
    saveToken(makeToken({ exp: Math.floor(Date.now() / 1000) - 10 }))
    render(<AuthProvider><Probe /></AuthProvider>)
    expect(screen.getByTestId('user')).toHaveTextContent('signed-out')
    expect(loadToken()).toBeNull()
  })

  it('signs in, ignores expired tokens, and signs out', () => {
    render(<AuthProvider><Probe /></AuthProvider>)
    act(() => screen.getByText('sign in expired').click())
    expect(screen.getByTestId('user')).toHaveTextContent('signed-out')

    act(() => screen.getByText('sign in').click())
    expect(screen.getByTestId('user')).toHaveTextContent('new@example.com')
    expect(loadToken()).not.toBeNull()

    act(() => screen.getByText('sign out').click())
    expect(screen.getByTestId('user')).toHaveTextContent('signed-out')
    expect(loadToken()).toBeNull()
  })

  it('signs out automatically when the token expires', () => {
    vi.useFakeTimers()
    try {
      saveToken(makeToken({ exp: Math.floor(Date.now() / 1000) + 120 })) // expires in 2 min (1 min skew)
      render(<AuthProvider><Probe /></AuthProvider>)
      expect(screen.getByTestId('user')).toHaveTextContent('alice@example.com')
      act(() => {
        vi.advanceTimersByTime(61_000)
      })
      expect(screen.getByTestId('user')).toHaveTextContent('signed-out')
    } finally {
      vi.useRealTimers()
    }
  })
})

// ---------- GoogleSignInButton ----------

function fakeGoogle() {
  let callback: ((response: CredentialResponse) => void) | null = null
  const google: GoogleIdentity = {
    initialize: vi.fn((config) => {
      callback = config.callback
    }),
    renderButton: vi.fn(),
    disableAutoSelect: vi.fn(),
  }
  return { google, sendCredential: (credential: string) => callback?.({ credential }) }
}

describe('GoogleSignInButton', () => {
  it('initializes Google with our client id and signs in from the callback', async () => {
    const { google, sendCredential } = fakeGoogle()
    render(
      <AuthProvider>
        <GoogleSignInButton clientId="test-client.apps.googleusercontent.com" loadGoogle={() => Promise.resolve(google)} />
        <Probe />
      </AuthProvider>,
    )
    await waitFor(() => expect(google.renderButton).toHaveBeenCalledOnce())
    expect(google.initialize).toHaveBeenCalledWith(expect.objectContaining({ client_id: 'test-client.apps.googleusercontent.com' }))

    act(() => sendCredential(makeToken({ email: 'google-user@example.com' })))
    expect(screen.getByTestId('user')).toHaveTextContent('google-user@example.com')
  })

  it('explains a missing client id', () => {
    render(<AuthProvider><GoogleSignInButton clientId="" loadGoogle={() => Promise.reject(new Error('unused'))} /></AuthProvider>)
    expect(screen.getByRole('alert')).toHaveTextContent('VITE_GOOGLE_CLIENT_ID')
  })

  it('shows an error if the Google script cannot load', async () => {
    render(
      <AuthProvider>
        <GoogleSignInButton clientId="x" loadGoogle={() => Promise.reject(new Error('Could not load Google sign-in.'))} />
      </AuthProvider>,
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load Google sign-in')
  })
})
