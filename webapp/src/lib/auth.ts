/**
 * Auth utilities: token storage, user info, apiFetch wrapper.
 * All API calls should go through apiFetch() to auto-inject the JWT
 * and handle 401 (auto-logout).
 */

const TOKEN_KEY = 'editoria_token'
const USER_KEY = 'editoria_user'

export interface AuthUser {
  id: string
  email: string
  name: string | null
  role: 'user' | 'admin'
  monthly_limit_usd: number
  current_month_spend_usd: number
  credits_remaining_usd: number | null
  spent_eur: number
  limit_eur: number | null
  credits_remaining_eur: number | null
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function setAuth(token: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * Drop-in fetch() replacement that:
 * - Automatically injects the Authorization header
 * - On 401 → clears auth and reloads the page (shows login)
 */
export async function apiFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken()
  const existingHeaders = (options.headers as Record<string, string>) || {}
  const headers: Record<string, string> = {
    ...existingHeaders,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  const response = await fetch(url, { ...options, headers })
  if (response.status === 401) {
    clearAuth()
    window.location.href = '/'
  }
  return response
}
