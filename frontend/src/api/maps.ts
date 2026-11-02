/** 三子系统共用地图；使用V1规范入口。 */
import { API_BASE_URL, API_PATHS } from '@/config/endpoints'
export function mapImageUrl(points: string, start?: number) {
  return API_BASE_URL + API_PATHS.STATIC_MAP + '?points=' + encodeURIComponent(points) + (start == null ? '' : '&start=' + start)
}
