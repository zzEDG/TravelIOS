import { request, TravelError } from './http'
import { currentUser, authReady, csrfToken, clearUserData, type AuthUser } from './auth-state'
interface AuthEnvelope<T> { code: number; message: string; data: T }
let restoring: Promise<void> | undefined
export async function restoreAuth() {
  if (authReady.value) return
  if (!restoring) restoring = (async () => {
    try {
      const response = await request<AuthEnvelope<AuthUser>>('/api/v1/auth/me')
      currentUser.value = response.data
      authReady.value = true
    } catch (error) {
      if (!(error instanceof TravelError) || error.status !== 401) throw error
      clearUserData(); authReady.value = true
    } finally { restoring = undefined }
  })()
  await restoring
}
export async function login(username: string, password: string) {
  const response = await request<AuthEnvelope<{user: AuthUser; csrf_token: string}>>('/api/v1/auth/login', {username, password})
  clearUserData()
  currentUser.value = response.data.user
  csrfToken.value = response.data.csrf_token
  authReady.value = true
  broadcastChange()
}
export async function register(username: string, password: string) {
  await request('/api/v1/auth/register', {username, password})
}
export async function logout() {
  await request('/api/v1/auth/logout', {})
  clearUserData(); authReady.value = true
  broadcastChange()
}
function broadcastChange() {
  try { localStorage.setItem('travelOS:authChanged', String(Date.now())) } catch { /* Storage is optional. */ }
}
