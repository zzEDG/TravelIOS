/** 唯一HTTP客户端，统一超时及后端错误转换。 */
import axios from 'axios'
import { showError } from './error-dialog'
import { csrfToken, clearUserData, authReady, currentUser } from './auth-state'
import { selectedModel, modelWarning } from './model'
import { API_BASE_URL } from '@/config/endpoints'
const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})
export class TravelError extends Error {
  constructor(message: string, public status?: number) { super(message) }
}
export async function request<T>(path: string, payload?: Record<string, unknown>, method?: 'DELETE'): Promise<T> {
  try {
    // 多类别按队列检索，给后端完成同步模型调用及资源清理的时间。
    const planning = !!payload && /\/(?:travel|(?:citywalk|attraction)\/plan|food\/search|walk\/plan)$/.test(path)
    const headers: Record<string, string> = { 'X-AI-Model': selectedModel.value }
    if ((payload || method === 'DELETE') && !/\/auth\/(login|register)$/.test(path)) {
      if (!csrfToken.value) {
        const result = await client.get('/api/v1/auth/csrf')
        csrfToken.value = result.data.data.csrf_token
      }
      headers['X-CSRF-Token'] = csrfToken.value
    }
    const config = { headers, timeout: planning ? 480000 : 300000 }
    const response = method === 'DELETE' ? await client.delete<T>(path, config) : payload ? await client.post<T>(path, payload, config) : await client.get<T>(path, config)
    return response.data
  } catch (error) {
    if (axios.isAxiosError(error)) {
      const body = error.response?.data
      const wasLoggedIn = !!currentUser.value
      if (error.response?.status === 401 && !path.endsWith('/auth/login')) {
        clearUserData(); authReady.value = true
        window.dispatchEvent(new Event('travelos:auth-expired'))
      }
      if (body?.code === 50007) modelWarning.value = body.message || '当前 AI 服务调用失败，请切换模型，再重新提交。表单内容已保留。'

      const detail = body?.details?.map((d: {field: string; message: string}) => d.field + ': ' + d.message).join('；')
      const fallback = error.code === 'ECONNABORTED' ? '请求超时，请读取最新行程确认是否已保存，再重试' : '无法连接后端，请确认服务已启动'
      const message = [body?.message || (typeof body?.detail === 'string' ? body.detail : fallback), detail].filter(Boolean).join('：')
      // 首次访问时未登录是正常状态，由路由跳转登录页，不提示错误。
      if (!(path.endsWith('/auth/me') && error.response?.status === 401 && !wasLoggedIn)) showError(message)
      throw new TravelError(message, error.response?.status)
    }
    throw error
  }
}
