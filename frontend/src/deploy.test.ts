// Guards the production security headers in vercel.json (a typo here would either break
// Google sign-in in production or silently weaken the Content-Security-Policy).
import { describe, expect, it } from 'vitest'
import vercelJson from '../vercel.json'

interface VercelConfig {
  rewrites: { source: string; destination: string }[]
  headers: { source: string; headers: { key: string; value: string }[] }[]
}

const config: VercelConfig = vercelJson
const headers = Object.fromEntries(config.headers[0].headers.map((h) => [h.key, h.value]))
const csp = Object.fromEntries(
  headers['Content-Security-Policy'].split(';').map((directive) => {
    const [name, ...sources] = directive.trim().split(/\s+/)
    return [name, sources]
  }),
)

describe('vercel.json', () => {
  it('serves index.html for client-side routes like /runs/:id', () => {
    expect(config.rewrites).toEqual([{ source: '/(.*)', destination: '/index.html' }])
  })

  it('allows exactly what Google Identity Services needs', () => {
    expect(csp['script-src']).toContain('https://accounts.google.com/gsi/client')
    expect(csp['frame-src']).toContain('https://accounts.google.com/gsi/')
    expect(csp['style-src']).toContain('https://accounts.google.com/gsi/style')
    expect(csp['connect-src']).toContain('https://accounts.google.com/gsi/')
  })

  it('lets the app call an https API', () => {
    expect(csp['connect-src'].some((source) => /^https:\/\/.+\.onrender\.com$/.test(source))).toBe(true)
  })

  it('keeps the policy strict', () => {
    const all = Object.values(csp).flat()
    expect(all).not.toContain("'unsafe-eval'")
    expect(csp['script-src']).not.toContain("'unsafe-inline'")
    expect(csp['default-src']).toEqual(["'self'"])
    expect(csp['object-src']).toEqual(["'none'"])
    expect(csp['frame-ancestors']).toEqual(["'none'"])
    expect(headers['X-Content-Type-Options']).toBe('nosniff')
    expect(headers['X-Frame-Options']).toBe('DENY')
  })
})
