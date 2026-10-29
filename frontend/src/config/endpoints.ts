/** 后端接口地址集中定义，与页面路由分开。 */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')
const V1 = '/api/v1'
export const API_PATHS = {
  CITYWALK_PLAN: V1 + '/citywalk/plan',
  ATTRACTION_PLAN: V1 + '/attraction/plan',
  FOOD_SEARCH: V1 + '/food/search',
  TRAVEL: V1 + '/travel',
  LOCATION_SEARCH: V1 + '/locations/search',
  TRIP: (id: string) => V1 + '/trips/' + encodeURIComponent(id),
  TRIPS: V1 + '/trips',
  TRIP_HISTORY: (id: string) => V1 + '/trips/' + encodeURIComponent(id) + '/history',
  STATIC_MAP: V1 + '/maps/static',
  LEGACY_WALK_PLAN: '/api/walk/plan',
} as const
