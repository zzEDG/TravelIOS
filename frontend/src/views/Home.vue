<template>
  <main class="travel-page">
    <section class="intro"><div class="eyebrow">TRAVELOS · 城市探索助手</div><h1>走一段路，逛一个馆，找一顿好饭。</h1><p>选择一个目标，从真实地点和步行路线开始规划。</p>
      <a-button v-if="lastId" @click="router.push({name:ROUTE_NAMES.TRIP_DETAIL,params:{id:lastId}})">恢复上次行程</a-button>
    </section>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-alert v-if="snapshot" type="info" show-icon class="notice" :message="continuing ? '正在修改版本 ' + snapshot.state.state_version + '，保存后生成新版本，历史版本保留' : '已切换目标，下列表单将新建行程。若想沿用原行程，请在结果页用对话切换。'">
      <template #description><a-button size="small" :disabled="busy" @click="newTrip">新建行程</a-button> <a-button size="small" @click="router.push({name:ROUTE_NAMES.TRIP_DETAIL,params:{id:snapshot.state.trip_id}})">查看当前结果</a-button></template>
    </a-alert>
    <a-card :bordered="false" class="travel-card">
      <a-tabs v-model:activeKey="mode">
        <a-tab-pane key="CITYWALK" tab="城市漫步" :disabled="busy" /><a-tab-pane key="ATTRACTION" tab="景点游览" :disabled="busy" /><a-tab-pane key="FOOD" tab="餐厅推荐" :disabled="busy" /><a-tab-pane key="CHAT" tab="对话规划" :disabled="busy" />
      </a-tabs>
      <PlanHistory :skill="mode === 'CHAT' ? undefined : mode" :disabled="busy" @reload="editReload++" />
      <h2>{{ continuing ? '修改规划' : '新建规划' }}</h2>
      <a-form :model="form" layout="vertical" @finish="submit">
        <fieldset :disabled="busy" class="form-fields">
          <ModelSelector :disabled="busy" />
          <a-row :gutter="20">
            <a-col :xs="24" :md="8"><a-form-item label="城市"><a-input v-model:value="form.city" :maxlength="30" :disabled="busy" /></a-form-item></a-col>
            <a-col :xs="24" :md="16"><a-form-item label="从哪里出发？"><a-input v-model:value="startText" :maxlength="100" :disabled="busy || searching" placeholder="例如：人民广场地铁站、静安寺或南京西路100号" /></a-form-item></a-col>
          </a-row>
          <a-button :loading="searching" :disabled="busy || !startText.trim()" @click="findStart">搜索起点</a-button>
          <p v-if="selectedOrigin" class="chosen">已选起点：{{ selectedOrigin.name }}<br /><span class="muted">{{ selectedOrigin.address }}</span></p>
          <p v-else class="muted">输入城市和地名/地址，搜索后选择正确位置。程序会自动取得坐标。</p>
          <div v-if="matches.length" class="location-matches">
            <button v-for="place in matches" :key="place.id" type="button" :disabled="busy" class="location-option" @click="chooseStart(place)">
              <strong>{{ place.name }}</strong><span>{{ place.address || place.category }}</span><span class="muted">选择这个位置 →</span>
            </button>
          </div>
          <template v-if="mode === 'CITYWALK'">
            <a-form-item label="行程名称（可选，不限制地点类别）"><a-input v-model:value="form.theme" :maxlength="40" :disabled="busy" placeholder="例如：周末散步" /></a-form-item>
            <a-form-item label="避开商业场所"><a-switch v-model:checked="form.avoid_commercial" :disabled="busy" /></a-form-item>
          </template>
          <template v-if="mode === 'CITYWALK' || mode === 'ATTRACTION'">
            <a-row :gutter="20"><a-col :xs="24" :md="8"><a-form-item :label="mode === 'ATTRACTION' ? '可用时长（小时，供推荐参考）' : '可用时长（小时）'"><a-input-number v-model:value="form.available_hours" :min="mode === 'CITYWALK' ? 2 : 0.5" :max="mode === 'CITYWALK' ? 8 : 12" :step="mode === 'CITYWALK' ? 1 : 0.5" :disabled="busy" /></a-form-item></a-col>
              <a-col :xs="24" :md="8"><a-form-item label="目标地点数"><a-input-number v-model:value="form.target_stops" :min="mode === 'CITYWALK' ? 2 : 1" :max="5" :disabled="busy" /></a-form-item></a-col>
              <a-col v-if="mode === 'CITYWALK'" :xs="24" :md="8"><a-form-item label="总步行上限（米，可留空）"><a-input-number v-model:value="form.max_walking_meters" :min="mode === 'CITYWALK' ? 500 : 100" :max="30000" :disabled="busy" /></a-form-item></a-col></a-row>
            <a-form-item :label="mode === 'CITYWALK' ? '想去的地方（重要的放前面，用逗号分隔）' : '兴趣（用逗号分隔）'"><a-input v-model:value="form.interests" placeholder="公园，咖啡馆，书店" :disabled="busy" /></a-form-item>
            <a-form-item label="不喜欢（用逗号分隔）"><a-input v-model:value="form.dislikes" :disabled="busy" /></a-form-item>
            <a-form-item label="出行日期（可选）"><input v-model="form.travel_date" type="date" class="date-input" /></a-form-item>
          </template>
          <template v-if="mode === 'CITYWALK'">
            <a-form-item label="游览先后（可选，按顺序用逗号分隔）"><a-input v-model:value="form.visit_order" placeholder="例如：公园，书店；留空则按位置与偏好逐站安排" :disabled="busy" /></a-form-item>
            <a-form-item label="最后一站想去（可选）"><a-input v-model:value="form.end_with" :maxlength="40" placeholder="例如：咖啡馆" :disabled="busy" /></a-form-item>
            <p class="muted">偏好主次与游览顺序分别设置。可以以公园为主、最后去咖啡馆；新增偏好不会自动增加总地点数。</p>
          </template>
          <template v-if="mode === 'ATTRACTION'">
            <a-form-item label="搜索半径（米）"><a-input-number v-model:value="form.radius_meters" :min="500" :max="10000" :disabled="busy" /></a-form-item>
            <a-form-item label="查询天气"><a-switch v-model:checked="form.consider_weather" :disabled="busy" /></a-form-item>
            <p class="muted">景点按偏好匹配度排序，展示各景点距起点的直线距离。</p>
          </template>
          <template v-if="mode === 'FOOD'">
            <a-row :gutter="20"><a-col :xs="24" :md="8"><a-form-item label="餐次"><a-select v-model:value="form.meal_type" :options="meals" :disabled="busy" /></a-form-item></a-col>
              <a-col :xs="24" :md="8"><a-form-item label="搜索半径（米，直线）"><a-input-number v-model:value="form.max_distance_meters" :min="100" :max="10000" :disabled="busy" /></a-form-item></a-col>
              <a-col :xs="24" :md="8"><a-form-item label="备选餐厅数"><a-input-number v-model:value="form.food_limit" :min="1" :max="5" :disabled="busy" /></a-form-item></a-col></a-row>
            <a-form-item label="菜系偏好（用逗号分隔）"><a-input v-model:value="form.cuisine_preference" placeholder="本帮菜，粤菜" :disabled="busy" /></a-form-item>
            <a-form-item label="忌口与口味（用逗号分隔）"><a-input v-model:value="form.food_avoid" placeholder="尽量少辣" :disabled="busy" /></a-form-item>
            <p class="muted">餐厅按偏好匹配度排序，展示距起点的直线距离；忌口需向餐厅确认。</p>
          </template>
          <a-form-item v-if="mode === 'CHAT'" label="告诉我你的目标"><a-textarea v-model:value="chat" :rows="4" :maxlength="1000" placeholder="找起点周边1500米内的本帮菜餐厅" :disabled="busy" /></a-form-item>
          <a-form-item v-else label="补充要求"><a-textarea v-model:value="form.additional_requirements" :rows="3" :maxlength="500" placeholder="例如：以逛公园为主，最后找咖啡店休息，尽量少走路" :disabled="busy" /></a-form-item>
          <a-alert v-if="clarification" type="info" :message="clarification" show-icon class="notice" />
          <a-button type="primary" html-type="submit" size="large" :loading="busy" block>{{ busy ? '正在检索地点、翻译与规划，请稍候…' : continuing ? '保存修改并重新规划' : '开始规划' }}</a-button>
        </fieldset>
      </a-form>
    </a-card>
  </main>
</template>
<script setup lang="ts">
import { ROUTE_NAMES } from '@/config/routes'
import PlanHistory from '@/components/PlanHistory.vue'
import ModelSelector from '@/components/ModelSelector.vue'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { searchLocations, createPlan, getTrip, lastTrip, rememberTrip, travel, TravelError } from '@/api/travel'
import type { Skill, Snapshot, LocationMatch } from '@/types/travel'
const route = useRoute(), router = useRouter()
const mode = ref<Skill | 'CHAT'>('CITYWALK'), busy = ref(false), error = ref(''), chat = ref(''), clarification = ref('')
const lastId = ref(lastTrip()), snapshot = ref<Snapshot | null>(null), pendingQuestion = ref('')
const editReload=ref(0)
const startText=ref(''), searching=ref(false), matches=ref<LocationMatch[]>([]), selectedOrigin=ref<LocationMatch | null>(null)
const meals = [{value:'breakfast',label:'早餐'},{value:'lunch',label:'午餐'},{value:'dinner',label:'晚餐'},{value:'snack',label:'小吃'}]
const defaults = () => ({city:'上海',theme:'自由漫步',available_hours:3 as number | null,target_stops:2 as number | null,max_walking_meters:null as number | null,avoid_commercial:false,interests:'',dislikes:'',visit_order:'',end_with:'',travel_date:'',additional_requirements:'',radius_meters:5000 as number | null,consider_weather:true,meal_type:'dinner',max_distance_meters:1500 as number | null,food_limit:3 as number | null,cuisine_preference:'',food_avoid:''})
const form = reactive(defaults())
const continuing = computed(() => !!snapshot.value && (mode.value === snapshot.value.state.current_skill || mode.value === 'CHAT'))
function newTrip() { snapshot.value = null; Object.assign(form, defaults()); error.value=''; clarification.value=''; pendingQuestion.value=''; chat.value=''; selectedOrigin.value=null; matches.value=[]; startText.value=''; router.replace({name:ROUTE_NAMES.HOME}) }
function list(value: string) { const result=value.split(/[,，、\n]/).map(v=>v.trim()).filter(Boolean); if(result.length>10 || result.some(v=>v.length>40)) throw new Error('偏好最多10项，每项不超过40字'); return result }
watch(() => [route.query.trip,route.query.version,editReload.value], async ([id,version], _old, onCleanup) => {
  if(typeof id !== 'string') return
  let active=true
  onCleanup(()=>{active=false})
  busy.value=true; error.value=''
  try {
    const selectedVersion=typeof version === 'string' ? Number(version) : undefined
    if(selectedVersion !== undefined && (!Number.isInteger(selectedVersion) || selectedVersion < 1)) throw new Error('规划版本无效')
    const response=await getTrip(id,selectedVersion)
    if(!active) return
    snapshot.value=response.data; clarification.value='';pendingQuestion.value='';chat.value=''
    const s=response.data.state
    Object.assign(form,s,{interests:s.interests.join('，'),dislikes:s.dislikes.join('，'),visit_order:(s.visit_order || []).join('，'),end_with:s.end_with || '',cuisine_preference:s.cuisine_preference.join('，'),food_avoid:s.food_avoid.join('，'),travel_date:s.travel_date || ''})
    startText.value=s.origin_name || '原行程起点'
    selectedOrigin.value={id:'saved-origin',name:startText.value,address:'沿用已保存起点，无需重新搜索；修改城市或位置后请重新选择。',category:'',location:s.current_location,source_url:null}
    matches.value=[]
    mode.value=s.current_skill
  } catch(e) { if(active) {snapshot.value=null;selectedOrigin.value=null;error.value=e instanceof Error ? e.message : '恢复失败'} } finally {if(active) busy.value=false}
},{immediate:true})

watch([()=>form.city,startText],()=>{selectedOrigin.value=null;matches.value=[]},{flush:'sync'})
async function findStart() {
  if(searching.value || busy.value) return
  if(form.city.trim().length<2 || !startText.value.trim()) {error.value='请填写城市和起点位置';return}
  const city=form.city.trim(),query=startText.value.trim()
  searching.value=true;error.value='';selectedOrigin.value=null;matches.value=[]
  try {
    const response=await searchLocations(city,query)
    if(form.city.trim()===city && startText.value.trim()===query) matches.value=response.data
  } catch(e) {error.value=e instanceof Error ? e.message : '搜索失败'} finally {searching.value=false}
}
function chooseStart(place: LocationMatch) {
  selectedOrigin.value=place;matches.value=[]
}

async function submit() {
  if(busy.value || searching.value) return
  error.value=''
  try {
    if(form.city.trim().length<2) throw new Error('请填写城市')
    if(!selectedOrigin.value) throw new Error('请先搜索并选择起点位置')
    const location=selectedOrigin.value.location
    const resume=continuing.value && snapshot.value ? {trip_id:snapshot.value.state.trip_id,state_version:snapshot.value.latest_version,base_version:snapshot.value.state.state_version} : {}
    let payload: Record<string,unknown>={...resume,city:form.city.trim(),origin_name:selectedOrigin.value.name,additional_requirements:form.additional_requirements}
    if(mode.value==='CHAT') {
      if(!chat.value.trim()) throw new Error('请填写规划需求')
      payload={...resume,city:form.city.trim(),location,origin_name:selectedOrigin.value.name,message:[pendingQuestion.value,chat.value.trim()].filter(Boolean).join('；').slice(-1000)}
    } else if(mode.value==='FOOD') {
      if(form.max_distance_meters==null || form.food_limit==null) throw new Error('请填写距离和餐厅数')
      Object.assign(payload,{center_location:location,meal_type:form.meal_type,cuisine_preference:list(form.cuisine_preference),avoid:list(form.food_avoid),max_distance_meters:form.max_distance_meters,limit:form.food_limit})
    } else {
      if(form.available_hours==null || form.target_stops==null) throw new Error('请填写时长和地点数')
      Object.assign(payload,{available_hours:form.available_hours,target_stops:form.target_stops,max_walking_meters:mode.value === 'CITYWALK' ? form.max_walking_meters : null,interests:list(form.interests),dislikes:list(form.dislikes),travel_date:form.travel_date || null})
      if(mode.value==='CITYWALK') {
        if(!form.interests.trim() && !form.additional_requirements.trim() && !form.visit_order.trim() && !form.end_with.trim()) throw new Error('请填写想去的地方或补充要求')
        Object.assign(payload,{start_location:location,theme:form.theme.trim() || '自由漫步',avoid_commercial:form.avoid_commercial,visit_order:list(form.visit_order),end_with:form.end_with.trim() || null})
      }
      else { if(form.radius_meters==null) throw new Error('请填写搜索半径'); Object.assign(payload,{location,radius_meters:form.radius_meters,consider_weather:form.consider_weather}) }
    }
    busy.value=true
    const response=mode.value==='CHAT' ? await travel(payload) : await createPlan(mode.value,payload)
    if(response.data.routed_skill==='CLARIFY') { clarification.value=response.data.question; pendingQuestion.value=String(payload.message)+'；'+response.data.question; chat.value=''; return }
    rememberTrip(response.data.trip_id); lastId.value=response.data.trip_id
    await router.push({name:ROUTE_NAMES.TRIP_DETAIL,params:{id:response.data.trip_id}})
  } catch(e) {
    error.value=e instanceof Error ? e.message : '规划失败'
    if(e instanceof TravelError && e.status===409) error.value+='。请从历史列表重新载入后再修改。'
  } finally {busy.value=false}
}
</script>
<style scoped>
.chosen {background:#edf5ef;padding:12px;border-radius:8px;margin:16px 0;}
.location-matches {display:grid;gap:8px;margin:16px 0;}
.location-option {display:flex;flex-direction:column;gap:5px;text-align:left;background:white;border:1px solid #dce8df;border-radius:8px;padding:12px;cursor:pointer;color:#254c3d;}
.location-option:hover {border-color:#247b5d;background:#f0f7f2;}
.intro { padding:28px 0; }.intro h1 { font-size:clamp(26px,4vw,40px);margin:12px 0; }.intro p {color:#667d73;}
.form-fields {border:0;padding:0;margin:0;min-width:0;}
:deep(.ant-input-number) {width:100%;}.date-input {width:100%;padding:8px;border:1px solid #d9d9d9;border-radius:6px;}
</style>
