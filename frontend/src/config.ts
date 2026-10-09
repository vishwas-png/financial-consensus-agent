// Build-time settings from frontend/.env (Vite exposes only VITE_* variables).
export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
export const GOOGLE_CLIENT_ID: string = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''
