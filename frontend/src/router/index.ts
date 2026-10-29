/** 页面路由注册入口；使用名称跳转，按页面延迟加载。 */
import { createRouter, createWebHistory } from 'vue-router'
import { PAGE_PATHS, ROUTE_NAMES } from '@/config/routes'
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: PAGE_PATHS.HOME, name: ROUTE_NAMES.HOME, component: () => import('@/views/Home.vue') },
    { path: PAGE_PATHS.TRIP_DETAIL, name: ROUTE_NAMES.TRIP_DETAIL, component: () => import('@/views/TravelResult.vue') },
    { path: PAGE_PATHS.LEGACY_RESULT, name: ROUTE_NAMES.LEGACY_RESULT, component: () => import('@/views/Result.vue') },
    { path: '/:pathMatch(.*)*', redirect: { name: ROUTE_NAMES.HOME } },
  ],
})
