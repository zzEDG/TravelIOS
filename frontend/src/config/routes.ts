/** 页面URL集中定义；业务组件按路由名称跳转。 */
export const ROUTE_NAMES = {
  HOME: 'Home',
  LOGIN: 'Login',
  MY_TRIPS: 'MyTrips',
  TRIP_DETAIL: 'TravelResult',
  LEGACY_RESULT: 'Result',
} as const
export const PAGE_PATHS = {
  HOME: '/',
  LOGIN: '/login',
  MY_TRIPS: '/my-trips',
  TRIP_DETAIL: '/trips/:id',
  LEGACY_RESULT: '/result',
} as const
