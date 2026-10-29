/** 旧版接口只作兼容，新业务使用travel.ts。 */
import type { WalkRequest, WalkResponse } from '@/types'
import { API_PATHS } from '@/config/endpoints'
import { request } from './http'
export function createWalk(data: WalkRequest) {
  return request<WalkResponse>(API_PATHS.LEGACY_WALK_PLAN, {...data})
}
