/** 三子系统、统一对话、行程恢复与起点搜索。 */
import type { Envelope, TravelPlan, Clarification, Snapshot, Skill, LocationMatch, HistoryPage } from '@/types/travel'
import { API_PATHS } from '@/config/endpoints'
import { request, TravelError } from './http'
export { TravelError } from './http'
async function v1Request<T>(path: string, payload?: Record<string, unknown>) {
  const response = await request<Envelope<T>>(path, payload)
  if (response.code !== 0) throw new TravelError(response.message)
  return response
}
export function createPlan(skill: Skill, payload: Record<string, unknown>) {
  const paths = { CITYWALK: API_PATHS.CITYWALK_PLAN, ATTRACTION: API_PATHS.ATTRACTION_PLAN, FOOD: API_PATHS.FOOD_SEARCH }
  return v1Request<TravelPlan>(paths[skill], payload)
}
export function travel(payload: Record<string, unknown>) { return v1Request<TravelPlan | Clarification>(API_PATHS.TRAVEL, payload) }
export function getTrip(id: string, version?: number) { return v1Request<Snapshot>(API_PATHS.TRIP(id) + (version ? '?version=' + version : '')) }
export function getHistory(options: { skill?: Skill; tripId?: string; query?: string; page?: number }) {
  const params = new URLSearchParams({page: String(options.page || 1), page_size: '5'})
  if(options.skill) params.set('skill', options.skill)
  if(options.query) params.set('q', options.query)
  return v1Request<HistoryPage>((options.tripId ? API_PATHS.TRIP_HISTORY(options.tripId) : API_PATHS.TRIPS) + '?' + params)
}
export function searchLocations(city: string, query: string) { return v1Request<LocationMatch[]>(API_PATHS.LOCATION_SEARCH, {city,query}) }
export function rememberTrip(id: string) { try { localStorage.setItem('travelOS:lastTrip', id) } catch { /* 后端保存仍然有效 */ } }
export function lastTrip(): string | null { try { return localStorage.getItem('travelOS:lastTrip') } catch { return null } }
