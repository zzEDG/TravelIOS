/** 页面URL集中定义；业务组件按路由名称跳转。 */
export const ROUTE_NAMES = {
  HOME: 'Home',
  TRIP_DETAIL: 'TravelResult',
  LEGACY_RESULT: 'Result',
} as const
export const PAGE_PATHS = {
  HOME: '/',
  TRIP_DETAIL: '/trips/:id',
  LEGACY_RESULT: '/result',
} as const
