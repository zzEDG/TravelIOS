<template>
  <section class="history" aria-label="规划历史">
    <div class="history-header">
      <div><h2>{{ tripId ? '历次修改' : '我的规划历史' }}</h2><p class="muted">{{ tripId ? '选择一个版本查看，或载入它的偏好重新修改。' : '查看以前的规划，直接载入修改。' }}</p></div>
      <a-button :loading="loading" @click="load">刷新</a-button>
    </div>
    <a-input-search v-if="!tripId" v-model:value="search" placeholder="搜索城市、规划标题或起点" :maxlength="100" allow-clear @search="searchHistory" />
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-spin :spinning="loading">
      <div v-if="items.length" class="history-items">
        <article v-for="item in items" :key="item.trip_id + ':' + item.state_version" class="history-item">
          <div class="history-copy">
            <div class="history-meta">{{ skillNames[item.skill] }} · {{ item.city }} · 版本 {{ item.state_version }}<a-tag v-if="item.expired" color="orange">可重新规划</a-tag></div>
            <h3>{{ item.title }}</h3><p class="history-summary">{{ item.summary }}</p>
            <p class="muted">{{ item.origin_name || '已保存起点' }} · {{ item.place_count }} {{ item.skill === 'FOOD' ? '家餐厅' : '个地点' }} · {{ formatDate(item.updated_at) }}</p>
          </div>
          <div class="history-actions">
            <a-button :disabled="disabled" @click="router.push({name:ROUTE_NAMES.TRIP_DETAIL,params:{id:item.trip_id},query:{version:item.state_version}})">查看</a-button>
            <a-popconfirm v-if="!tripId" title="删除这份规划及全部历史版本？此操作无法撤销。" ok-text="删除" cancel-text="取消" :disabled="disabled || !!deleting" @confirm="remove(item)">
              <a-button danger :disabled="disabled || !!deleting" :loading="deleting === item.trip_id">删除</a-button>
            </a-popconfirm>
            <a-button type="primary" :disabled="disabled" @click="edit(item)">载入修改</a-button>
          </div>
        </article>
      </div>
      <a-empty v-else-if="!loading && !error" :description="query ? '没有匹配的规划历史' : '暂无规划历史，完成规划后会自动保存'" class="history-empty" />
    </a-spin>
    <a-pagination v-if="total > 5" v-model:current="page" :total="total" :page-size="5" :show-size-changer="false" size="small" @change="load" />
  </section>
</template>
<script setup lang="ts">
import { showError } from '@/api/error-dialog'
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getHistory, deleteTrip } from '@/api/travel'
import { ROUTE_NAMES } from '@/config/routes'
import { skillNames } from '@/types/travel'
import type { Skill, HistoryItem } from '@/types/travel'
const props = defineProps<{skill?: Skill; tripId?: string; disabled?: boolean; refreshKey?: number}>()
const emit = defineEmits<{reload: []}>()
const router = useRouter(), items = ref<HistoryItem[]>([]), total = ref(0), page = ref(1)
const search = ref(''), query = ref(''), loading = ref(false), error = ref('')
const deleting = ref('')
let requestId = 0
watch(error, value => { if (value) showError(value) })
async function remove(item: HistoryItem) {
  if (deleting.value) return
  deleting.value = item.trip_id; error.value = ''
  try {
    await deleteTrip(item.trip_id)
    if (page.value > 1 && items.value.every(row => row.trip_id === item.trip_id)) page.value--
    await load()
    if (page.value > 1 && !items.value.length && !error.value) { page.value = Math.max(1, Math.ceil(total.value / 5)); await load() }
  } catch(e) { error.value = e instanceof Error ? e.message : '删除失败，请重试' }
  finally { deleting.value = '' }
}
function edit(item: HistoryItem) {
  const current=router.currentRoute.value
  if(current.name === ROUTE_NAMES.HOME && current.query.trip === item.trip_id && current.query.version === String(item.state_version)) emit('reload')
  else router.push({name:ROUTE_NAMES.HOME,query:{trip:item.trip_id,version:item.state_version}})
}
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', {hour12:false}) }
function searchHistory() { query.value=search.value.trim(); page.value=1; load() }
async function load() {
  const id = ++requestId
  loading.value=true; error.value=''
  try {
    const response = await getHistory({skill:props.skill,tripId:props.tripId,query:query.value,page:page.value})
    if(id !== requestId) return
    items.value=response.data.items;total.value=response.data.total
  } catch(e) {
    if(id === requestId) {items.value=[]; error.value=e instanceof Error ? e.message : '历史读取失败'}
  } finally {if(id === requestId) loading.value=false}
}
watch(() => [props.skill,props.tripId,props.refreshKey], () => {
  page.value=1; search.value='';query.value='';items.value=[];total.value=0;load()
}, {immediate:true})
</script>
<style scoped>
.history {margin:8px 0 24px;}.history-header {display:flex;align-items:center;justify-content:space-between;gap:16px;}.history-header h2 {margin:0;font-size:21px;}.history-header p {margin:6px 0 14px;}
.history-items {margin:14px 0;display:grid;gap:10px;}.history-item {display:flex;align-items:center;gap:20px;justify-content:space-between;border:1px solid #dce8df;background:#fafcf9;border-radius:12px;padding:16px;}
.history-copy {min-width:0;}.history-meta {font-size:12px;color:#577768;}.history-copy h3 {margin:8px 0;font-size:17px;overflow-wrap:anywhere;}.history-summary {margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.6;}.history-copy .muted {margin-bottom:0;}.history-actions {display:flex;gap:8px;flex-shrink:0;}.history-empty {padding:20px 0;}
@media(max-width:650px) {.history-item {align-items:stretch;flex-direction:column;gap:12px;}.history-actions {justify-content:flex-end;}}
</style>
