import { GoogleSignInButton } from '../auth/GoogleSignInButton'

export function LoginPage() {
  return (
    <main className="login">
      <h1>Financial Consensus Agent</h1>
      <p>
        Ask about a US stock and watch an Analyst and a Critic debate it live, using market data and the company's
        latest 10-K. An Auditor then fact-checks every claim against that evidence.
      </p>
      <p className="muted">Not investment advice. Sign in to start.</p>
      <GoogleSignInButton />
    </main>
  )
}
