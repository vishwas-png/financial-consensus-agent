import { useMemo } from 'react'
import { useAuth } from '../auth/context'
import { API_URL } from '../config'
import { createApiClient } from './client'

/** The API client for the signed-in user. A 401 from any call signs the user out. */
export function useApi() {
  const { token, signOut } = useAuth()
  return useMemo(
    () => createApiClient({ baseUrl: API_URL, getToken: () => token, onUnauthorized: signOut }),
    [token, signOut],
  )
}
