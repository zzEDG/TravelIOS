/** 唯一HTTP客户端，统一超时及后端错误转换。 */
import axios from 'axios'
import { selectedModel, modelWarning } from './model'
import { API_BASE_URL } from '@/config/endpoints'
const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})
export class TravelError extends Error {
  constructor(message: string, public status?: number) { super(message) }
}
export async function request<T>(path: string, payload?: Record<string, unknown>): Promise<T> {
  try {
    // 多类别按队列检索，给后端完成同步模型调用及资源清理的时间。
    const planning = !!payload && /\/(?:travel|(?:citywalk|attraction)\/plan|food\/search|walk\/plan)$/.test(path)
    const config = { headers: { 'X-AI-Model': selectedModel.value }, timeout: planning ? 480000 : 300000 }
    const response = payload ? await client.post<T>(path, payload, config) : await client.get<T>(path, config)
    return response.data
  } catch (error) {
    if (axios.isAxiosError(error)) {
      const body = error.response?.data
      if (body?.code === 50007) modelWarning.value = body.message || '当前 AI 服务调用失败，请切换模型，再重新提交。表单内容已保留。'

      const detail = body?.details?.map((d: {field: string; message: string}) => d.field + ': ' + d.message).join('；')
      const fallback = error.code === 'ECONNABORTED' ? '请求超时，请读取最新行程确认是否已保存，再重试' : '无法连接后端，请确认服务已启动'
      throw new TravelError([body?.message || (typeof body?.detail === 'string' ? body.detail : fallback), detail].filter(Boolean).join('：'), error.response?.status)
    }
    throw error
  }
}
