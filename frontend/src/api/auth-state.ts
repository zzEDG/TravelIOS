import { ref } from 'vue'
export interface AuthUser { id: string; username: string; created_at: string }
export const currentUser = ref<AuthUser | null>(null)
export const authReady = ref(false)
export const csrfToken = ref('')
export function clearUserData() {
  currentUser.value = null
  csrfToken.value = ''
  try { localStorage.removeItem('travelOS:lastTrip'); sessionStorage.removeItem('cityWalkPlan') } catch { /* Storage is optional. */ }
}
