// Builds fake (unsigned) Google-style ID tokens for tests. The frontend only decodes the
// payload; signature verification happens on the backend.

function base64url(value: object): string {
  const bytes = new TextEncoder().encode(JSON.stringify(value))
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function makeToken(overrides: Record<string, unknown> = {}): string {
  const payload = {
    sub: 'alice-sub',
    email: 'alice@example.com',
    name: 'Alice Ändersson', // non-ASCII on purpose: decoding must handle UTF-8
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...overrides,
  }
  return `${base64url({ alg: 'RS256', typ: 'JWT' })}.${base64url(payload)}.fake-signature`
}
