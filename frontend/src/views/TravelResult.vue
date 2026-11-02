<template>
  <main class="travel-page">
    <div class="actions"><router-link :to="{name:ROUTE_NAMES.HOME}">← 返回规划与历史</router-link><a-button :loading="busy" :disabled="busy" @click="load">刷新已保存行程</a-button></div>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-spin :spinning="busy && !snapshot">
    <template v-if="snapshot">
      <a-alert v-if="plan.state_version < snapshot.latest_version" type="info" show-icon class="notice" :message="'正在查看历史版本 ' + plan.state_version + '，当前最新为版本 ' + snapshot.latest_version + '。修改后会保存为新版本。'" />
      <section class="result-hero">
        <img v-if="plan.cover" :src="plan.cover.url" :alt="plan.cover.alt" />
        <div class="hero-text"><div class="eyebrow">{{ skillNames[plan.routed_skill] }} · {{ snapshot.state.city }}</div><h1>{{ plan.title }}</h1><p>{{ plan.summary }}</p>
          <p v-if="snapshot.state.origin_name">起点：{{ snapshot.state.origin_name }}</p><div class="chips"><span>{{ plan.places.length }} {{ food ? '家备选餐厅' : '个地点' }}</span><span v-if="!food && !attraction">{{ plan.duration_semantics === 'WALKING_ONLY' ? '预计步行 ' + plan.estimated_minutes + ' 分钟（不含停留）' : plan.estimated_minutes + ' 分钟估计' }}</span><span v-if="!food && !attraction && plan.total_walking_meters !== null">步行 {{ plan.total_walking_meters }} 米</span><span>已保存 · 版本 {{ plan.state_version }}</span></div>
          <a v-if="plan.cover" :href="plan.cover.unsplash_url" target="_blank" rel="noopener noreferrer" class="credit">氛围示意图 · {{ plan.cover.photographer }} / Unsplash</a>
        </div>
      </section>
      <a-alert v-if="meta?.degraded_services.length" type="warning" show-icon class="notice" :message="'部分信息暂不可用：' + meta.degraded_services.map(v => ({WEATHER:'天气',UNSPLASH:'封面',AMAP_ROUTE:'部分路线',AMAP_RATE_LIMIT:'高德地图请求过于频繁，部分地点或路线未能查询，请稍后重试',NARRATOR:'详细讲述（已提供基础评价，可切换模型重新规划）'}[v] || v)).join('、')" />
      <a-alert v-if="plan.places.length < requestedCount" type="warning" show-icon class="notice"
        :message="'目标 ' + requestedCount + ' 个地点，实际找到 ' + plan.places.length + ' 个'"
        description="本次未达到目标数量，具体原因见下方提示。" />
      <a-card v-if="plan.narration" :bordered="false" class="travel-card narration-card">
        <h2>{{ skillNames[plan.routed_skill] }} · 讲述与评价</h2>
        <p v-if="plan.narration.user_request" class="muted">{{ plan.narration.based_on_version ? '本次调整要求' : '你的要求' }}：{{ plan.narration.user_request }}</p>
        <p class="narration-text">{{ plan.narration.evaluation }}</p>
        <template v-if="plan.narration.based_on_version">
          <h3>相较版本 {{ plan.narration.based_on_version }} 的调整</h3>
          <ul><li v-for="(change,index) in plan.narration.changes" :key="index">{{ change }}</li></ul>
        </template>
      </a-card>
      <a-card v-if="plan.routed_skill === 'CITYWALK'" :bordered="false" class="travel-card">
        <h2>本次偏好</h2>
        <p>重要程度：{{ snapshot.state.interests.join('、') || '按补充要求安排' }}</p>
        <p v-if="snapshot.state.visit_order?.length">先后要求：{{ snapshot.state.visit_order.join(' → ') }}</p>
        <p v-if="snapshot.state.end_with">最后一站：{{ snapshot.state.end_with }}</p>
        <p class="muted">下一站根据前一站位置、真实步行路线和剩余预算选择，最终顺序保留决策结果。</p>
      </a-card>
      <div class="result-grid">
        <section>
          <a-card :bordered="false" class="travel-card"><h2>{{ food ? '挑选一家餐厅' : attraction ? '推荐景点' : '你的游览顺序' }}</h2><p class="muted">{{ food ? '按偏好匹配度排序，距离均为餐厅到起点的直线距离，请选择其中一家。' : attraction ? '按偏好匹配度排序，距离均为景点到起点的直线距离。' : (plan.duration_semantics === 'WALKING_ONLY' ? '路线包含起点到第一站；停留建议仅供参考，不计入步行时长。' : '路线包含起点到第一站；停留时间为估计。') }}</p>
            <article v-for="place in plan.places" :key="place.poi.id" class="place">
              <div class="place-title"><span class="rank">{{ place.step_order }}</span><h3>{{ place.poi.name }}</h3></div>
              <p class="muted">{{ place.poi.category }}</p><p>{{ place.why_selected }}</p><p>{{ place.poi.address || '地址以高德为准' }}</p>
              <div v-if="food || attraction" class="route-info">距起点（直线）：{{ place.distance_from_origin_meters != null ? place.distance_from_origin_meters + ' 米' : '未记录，请重新规划' }}</div>
              <div v-else class="route-info">{{ food ? '从起点步行' : fromName(place.poi.id) + ' → 本站' }}：{{ routeText(place.poi.id) }}<span v-if="plan.duration_semantics !== 'WALKING_ONLY' && place.stay_minutes != null"> · 停留约 {{ place.stay_minutes }} 分钟</span></div>
              <p v-if="plan.duration_semantics === 'WALKING_ONLY' && stayAdvice(place.poi.id)" class="muted">停留建议：{{ stayAdvice(place.poi.id) }}</p>
              <a :href="place.poi.source_url || poiUrl(place.poi)" target="_blank" rel="noopener noreferrer">在高德查看地点 ↗</a><p v-if="place.poi.retrieved_at" class="muted">来源：高德 · {{ new Date(place.poi.retrieved_at).toLocaleString() }}</p>
            </article>
          </a-card>
          <a-card :bordered="false" class="travel-card"><h2>地点分布</h2><template v-if="mapGroups.length"><p v-if="mapGroups.length > 1" class="muted">第 {{ mapPage }} / {{ mapGroups.length }} 组，展示第 {{ (mapPage - 1) * 5 + 1 }}–{{ Math.min(mapPage * 5, plan.places.length) }} 个地点；每组均包含起点，地点编号与列表一致。</p><img v-if="!mapFailed" :key="mapPoints" :src="mapImageUrl(mapPoints, (mapPage - 1) * 5 + 1)" class="map-image" alt="起点与推荐地点分布" @error="mapFailed=true; showError('地图加载失败，请稍后重试或使用地点链接查看。')" /><a-empty v-else description="地图暂不可用，请使用地点链接" /><a-pagination v-if="mapGroups.length > 1" v-model:current="mapPage" :total="mapGroups.length" :page-size="1" :show-size-changer="false" size="small" /></template><a-empty v-else description="暂无地点可展示" /><p class="muted">{{ food || attraction ? '地图展示起点与推荐地点的位置。' : '这是地点分布图；步行路线以每段的高德规划结果为准。' }}</p></a-card>
        </section>
        <aside>
          <a-card :bordered="false" class="travel-card"><h2>继续调整</h2><p class="muted">载入这个版本的偏好修改；对话也可以切换到另外一个目标。保存后保留旧版本。</p><a-button block :disabled="busy" @click="router.push({name:ROUTE_NAMES.HOME,query:{trip:plan.trip_id,version:plan.state_version}})">载入这个版本修改</a-button>
            <a-form :model="{message:text}" layout="vertical" @finish="send" class="chat-form"><ModelSelector :disabled="busy" /><a-form-item label="追加需求"><a-textarea v-model:value="text" :rows="4" :maxlength="1000" :disabled="busy" placeholder="改成 5 个地点；再加两个；或改找附近餐厅" /></a-form-item><a-alert v-if="clarification" type="info" :message="clarification" show-icon class="notice" /><a-button type="primary" html-type="submit" :loading="busy" block>发送并更新行程</a-button></a-form>
            <p class="muted">{{ busy ? '正在处理，请等待完成。' : '成功后自动保存，失败保留原行程。' }}</p>
          </a-card>
          <a-card v-if="plan.weather" :bordered="false" class="travel-card"><h2>出行天气 · {{ plan.weather.source === 'QWEATHER' ? '和风' : '高德' }}</h2><p v-if="plan.weather.temperature_max_c != null || plan.weather.temperature_min_c != null">最低 {{ plan.weather.temperature_min_c ?? '未知' }} °C / 最高 {{ plan.weather.temperature_max_c ?? '未知' }} °C</p><p>{{ plan.weather.forecast_date }} · {{ plan.weather.day_condition }} · {{ plan.weather.day_temperature_c ?? '未知' }} °C</p><p class="muted">夜间 {{ plan.weather.night_condition }} · {{ plan.weather.night_temperature_c ?? '未知' }} °C</p><p v-if="plan.weather.precipitation_probability_day != null">白天降水概率 {{ Math.round(plan.weather.precipitation_probability_day * 100) }}%</p><p v-if="plan.weather.precipitation_probability_night != null">夜间降水概率 {{ Math.round(plan.weather.precipitation_probability_night * 100) }}%</p><a v-if="plan.weather.source === 'QWEATHER'" href="https://www.qweather.com" target="_blank" rel="noopener noreferrer">天气来源：和风天气 ↗</a><a v-else href="https://www.amap.com" target="_blank" rel="noopener noreferrer">天气来源：高德地图 ↗</a></a-card>
          <a-card :bordered="false" class="travel-card"><h2>出发前确认</h2><ul><li v-for="note in plan.notes" :key="note">{{ note }}</li></ul></a-card>
        </aside>
      </div>
      <a-card :bordered="false" class="travel-card"><PlanHistory :trip-id="plan.trip_id" :disabled="busy" :refresh-key="historyRefresh" /></a-card>
    </template>
    <a-empty v-else-if="!busy" description="未能读取行程"><router-link :to="{name:ROUTE_NAMES.HOME}">返回首页</router-link></a-empty>
    </a-spin>
  </main>
</template>
<script setup lang="ts">
import { showError } from '@/api/error-dialog'
import { ROUTE_NAMES } from '@/config/routes'
import PlanHistory from '@/components/PlanHistory.vue'
import ModelSelector from '@/components/ModelSelector.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getTrip, travel, rememberTrip, TravelError } from '@/api/travel'
import { mapImageUrl } from '@/api/maps'
import { skillNames } from '@/types/travel'
import type { Snapshot, Meta, TravelPlan } from '@/types/travel'
const route=useRoute(),router=useRouter(), snapshot=ref<Snapshot | null>(null),meta=ref<Meta | null>(null)
const busy=ref(false),error=ref(''),text=ref(''),clarification=ref(''),pending=ref(''),mapFailed=ref(false)
const historyRefresh=ref(0)
watch(error, value => { if (value) showError(value) })
let loadId=0
const plan=computed(()=>snapshot.value!.latest_plan),food=computed(()=>snapshot.value?.latest_plan.routed_skill==='FOOD')
const attraction=computed(()=>snapshot.value?.latest_plan.routed_skill==='ATTRACTION')
const requestedCount=computed(()=>snapshot.value ? (food.value ? snapshot.value.state.food_limit : snapshot.value.state.target_stops) : 0)
const mapPage=ref(1)
const mapGroups=computed(()=> {
  if (!snapshot.value) return []
  const places=plan.value.places
  return Array.from({length:Math.ceil(places.length / 5)}, (_, index) =>
    [plan.value.origin, ...places.slice(index * 5, (index + 1) * 5).map(p=>p.poi.location)]
      .map(p=>p.longitude.toFixed(6)+','+p.latitude.toFixed(6)).join(';'))
})
const mapPoints=computed(()=>mapGroups.value[mapPage.value-1] || '')
watch(mapPage,()=>{mapFailed.value=false})
async function load() {
  const id=++loadId
  busy.value=true;error.value=''
  try {
    const version=typeof route.query.version === 'string' ? Number(route.query.version) : undefined
    if(version !== undefined && (!Number.isInteger(version) || version < 1)) throw new Error('规划版本无效')
    const response=await getTrip(String(route.params.id),version)
    if(id !== loadId) return
    snapshot.value=response.data;meta.value=response.meta;rememberTrip(response.data.state.trip_id);mapPage.value=1;mapFailed.value=false
  } catch(e) {if(id===loadId) error.value=e instanceof Error ? e.message : '读取失败'} finally {if(id===loadId) busy.value=false}
}
watch(()=>[route.params.id,route.query.version],()=>{snapshot.value=null;clarification.value='';pending.value='';text.value='';load()},{immediate:true})
function stayAdvice(id:string) {return plan.value.narration?.stay_recommendations?.find(item=>item.poi_id===id)?.suggestion}
function routeText(id:string) {const r=plan.value.routes.find(r=>r.to_poi_id===id);return r?.distance_meters!=null && r.duration_minutes!=null ? r.distance_meters+' 米 / 约 '+r.duration_minutes+' 分钟' : '暂不可用'}
function fromName(id:string) {const r=plan.value.routes.find(r=>r.to_poi_id===id);return r?.from_poi_id ? plan.value.places.find(p=>p.poi.id===r.from_poi_id)?.poi.name || '上一站' : '起点'}
function poiUrl(poi:TravelPlan['places'][number]['poi']) {return 'https://uri.amap.com/marker?position='+poi.location.longitude+','+poi.location.latitude+'&name='+encodeURIComponent(poi.name)}
async function send() {
  if(busy.value || !snapshot.value) return
  if(!text.value.trim()) {error.value='请填写追加需求';return}
  busy.value=true;error.value=''
  try {
    const response=await travel({trip_id:plan.value.trip_id,state_version:snapshot.value.latest_version,base_version:plan.value.state_version,message:[pending.value,text.value.trim()].filter(Boolean).join('；').slice(-1000)})
    if(response.data.routed_skill==='CLARIFY') {clarification.value=response.data.question;pending.value=[pending.value,text.value,response.data.question].filter(Boolean).join('；');text.value='';return}
    let restored
    try { restored=await getTrip(response.data.trip_id) }
    catch { throw new Error('行程已保存，但读取新结果失败。请点击“刷新已保存行程”后继续修改') }
    snapshot.value=restored.data;meta.value=response.meta;mapPage.value=1;mapFailed.value=false;clarification.value='';pending.value='';text.value=''
    historyRefresh.value++
    await router.replace({name:ROUTE_NAMES.TRIP_DETAIL,params:{id:response.data.trip_id}})
  } catch(e) {
    error.value=e instanceof Error ? e.message : '更新失败'
    if(e instanceof TravelError && e.status===409) error.value+='。请点击“刷新已保存行程”，保留输入后再次发送。'
  } finally {busy.value=false}
}
</script>
<style scoped>
.actions {display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}
.result-hero {position:relative;overflow:hidden;background:linear-gradient(120deg,#173b35,#377c62);border-radius:22px;color:white;}
.result-hero>img {position:absolute;width:100%;height:100%;object-fit:cover;opacity:.25;}
.hero-text {position:relative;padding:36px;}.hero-text h1 {font-size:32px;}.hero-text p {line-height:1.8;}
.chips {display:flex;flex-wrap:wrap;gap:12px;}.chips span {padding:6px 12px;border:1px solid #ffffff70;border-radius:24px;}
.credit {display:block;color:white;margin-top:16px;font-size:12px;}
.narration-card {margin-top:22px;}.narration-text {white-space:pre-line;line-height:1.9;overflow-wrap:anywhere;}
.result-grid {display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,1fr);gap:22px;margin-top:22px;}
.place {border-bottom:1px solid #e1eae5;padding:16px 0;overflow-wrap:anywhere;}.place:last-child {border:0;}
.place-title {display:flex;align-items:center;gap:12px;}.place-title h3 {margin:0;font-size:20px;}
.rank {background:#247b5d;color:white;width:30px;height:30px;display:grid;place-items:center;border-radius:50%;flex-shrink:0;}
.route-info {background:#edf5ef;padding:10px;border-radius:8px;margin:12px 0;font-size:13px;}
.map-image {width:100%;border-radius:10px;}.chat-form {margin-top:18px;}li {line-height:1.8;margin:8px 0;}ul {padding-left:20px;}
@media(max-width:850px) {.result-grid {grid-template-columns:1fr;}.hero-text {padding:24px;}}
</style>
