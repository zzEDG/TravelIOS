/** 页面路由注册入口；使用名称跳转，按页面延迟加载。 */
import { createRouter, createWebHistory } from 'vue-router'
import { PAGE_PATHS, ROUTE_NAMES } from '@/config/routes'
import { restoreAuth } from '@/api/auth'
import { currentUser, authReady, clearUserData } from '@/api/auth-state'
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: PAGE_PATHS.LOGIN, name: ROUTE_NAMES.LOGIN, component: () => import('@/views/Login.vue') },
    { path: PAGE_PATHS.MY_TRIPS, name: ROUTE_NAMES.MY_TRIPS, component: () => import('@/views/MyTrips.vue') },
    { path: PAGE_PATHS.HOME, name: ROUTE_NAMES.HOME, component: () => import('@/views/Home.vue') },
    { path: PAGE_PATHS.TRIP_DETAIL, name: ROUTE_NAMES.TRIP_DETAIL, component: () => import('@/views/TravelResult.vue') },
    { path: PAGE_PATHS.LEGACY_RESULT, name: ROUTE_NAMES.LEGACY_RESULT, component: () => import('@/views/Result.vue') },
    { path: '/:pathMatch(.*)*', redirect: { name: ROUTE_NAMES.HOME } },
  ],
})

router.beforeEach(async to => {
  if (to.name === ROUTE_NAMES.LOGIN) return
  try { await restoreAuth() } catch { return { name: ROUTE_NAMES.LOGIN, query: { next: to.fullPath, reason: 'network' } } }
  if (!currentUser.value) return { name: ROUTE_NAMES.LOGIN, query: { next: to.fullPath } }
})
window.addEventListener('travelos:auth-expired', () => {
  if (router.currentRoute.value.name !== ROUTE_NAMES.LOGIN) void router.replace({name: ROUTE_NAMES.LOGIN, query: {next: router.currentRoute.value.fullPath}})
})
window.addEventListener('storage', event => {
  if (event.key === 'travelOS:authChanged') {
    clearUserData(); authReady.value = false
    // 丢弃旧账号的组件内快照，重新从会话获取身份。
    window.location.replace('/login')
  }
})
