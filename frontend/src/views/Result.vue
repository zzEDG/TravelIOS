<template>
  <main class="page">
    <div class="toolbar"><router-link :to="{name:ROUTE_NAMES.HOME}">← 重新规划</router-link><span>YOUR CITYWALK · 路线卡片</span></div>
    <template v-if="plan">
      <section class="hero">
        <img v-if="plan.cover" :src="plan.cover.url" :alt="plan.cover.alt" class="cover-image" />
        <div class="cover-shade"></div>
        <div class="hero-content">
          <div class="eyebrow">THEMED CITYWALK · {{ plan.city }}</div>
          <h1>{{ plan.title }}</h1>
          <p>{{ plan.introduction }}</p>
          <div class="pills"><span>⌖ {{ plan.stops.length }} 个地点</span><span>◷ 约 {{ plan.estimated_minutes }} 分钟</span><span>♧ {{ plan.theme }}</span></div>
        </div>
        <div v-if="plan.cover" class="credit">
          氛围示意图 · 摄影：<a :href="plan.cover.photographer_url" target="_blank" rel="noopener noreferrer">{{ plan.cover.photographer }}</a>
          / <a :href="plan.cover.unsplash_url" target="_blank" rel="noopener noreferrer">Unsplash</a>
        </div>
      </section>

      <div class="layout">
        <div class="main-column">
          <a-card :bordered="false" class="card route-card">
            <div class="section-top"><div><div class="eyebrow dark">YOUR ROUTE</div><h2>沿着这条线，慢慢走</h2></div><span class="muted">地点 A → {{ lastLabel }}</span></div>
            <div v-for="(stop, index) in plan.stops" :key="stop.poi_id" class="stop-row">
              <div class="rail"><span class="number">{{ String.fromCharCode(65 + index) }}</span><span v-if="index < plan.stops.length - 1" class="line"></span></div>
              <div class="stop-content">
                <div v-if="legForStop(index)" class="leg">🚶 从 {{ legForStop(index)?.from_name }} 步行至此 · {{ legText(legForStop(index)) }}</div>
                <div class="stop-heading"><h3>{{ stop.name }}</h3><span>{{ stop.stay_minutes }} 分钟停留</span></div>
                <div class="category">{{ stop.category }}</div>
                <p>{{ stop.reason }}</p>
                <div class="address">📍 {{ stop.address || '地址以高德地图为准' }}</div>
                <a :href="stop.amap_url" target="_blank" rel="noopener noreferrer" class="open-map">在高德查看地点 ↗</a>
              </div>
            </div>
          </a-card>
          <a-card :bordered="false" class="card">
            <div class="eyebrow dark">ON THE MAP</div><h2>地点分布</h2>
            <img v-if="!mapFailed" :src="mapImageUrl(plan.map_points)" alt="高德地图上的漫游地点分布" class="map-image" @error="mapFailed = true" />
            <a-empty v-else description="地图暂不可用，请使用每站的高德链接查看地点" />
            <p class="map-note">地图展示地点位置；步行距离与时长以高德路径规划结果为准。</p>
          </a-card>
        </div>
        <aside class="side-column">
          <a-card v-if="plan.weather" :bordered="false" class="card weather-card">
            <div class="eyebrow dark">WEATHER CHECK</div><h2>出行天气 · {{ plan.weather.source === 'QWEATHER' ? '和风' : '高德' }}</h2>
            <p v-if="plan.weather.temperature_max_c != null">最低 {{ plan.weather.temperature_min_c ?? '未知' }} °C / 最高 {{ plan.weather.temperature_max_c }} °C</p>
            <div class="weather-main">☀️ {{ plan.weather.day_weather }} <span>{{ plan.weather.day_temp }}°C</span></div>
            <div class="muted">{{ plan.weather.date }} · 夜间 {{ plan.weather.night_weather }} {{ plan.weather.night_temp }}°C</div>
            <p v-if="plan.weather.precipitation_probability_day != null">白天降水概率 {{ Math.round(plan.weather.precipitation_probability_day * 100) }}%</p>
            <a v-if="plan.weather.source === 'QWEATHER'" href="https://www.qweather.com" target="_blank" rel="noopener noreferrer">天气来源：和风天气 ↗</a><a v-else href="https://www.amap.com" target="_blank" rel="noopener noreferrer">天气来源：高德地图 ↗</a>
          </a-card>
          <a-card :bordered="false" class="card">
            <div class="eyebrow dark">PLAN B</div><h2>天气变化时</h2><p>{{ plan.rain_tip }}</p>
          </a-card>
          <a-card :bordered="false" class="card">
            <div class="eyebrow dark">BEFORE YOU GO</div><h2>出发前看一眼</h2>
            <ul><li v-for="note in plan.notes" :key="note">{{ note }}</li></ul>
          </a-card>
        </aside>
      </div>
    </template>
    <a-empty v-else description="还没有漫游路线"><router-link :to="{name:ROUTE_NAMES.HOME}">返回首页生成路线</router-link></a-empty>
  </main>
</template>

<script setup lang="ts">
import { ROUTE_NAMES } from '@/config/routes'
import { computed, ref } from 'vue'
import { mapImageUrl } from '@/api/maps'
import type { WalkLeg, WalkPlan } from '@/types'

let stored: WalkPlan | null = null
try { stored = JSON.parse(sessionStorage.getItem('cityWalkPlan') || 'null') as WalkPlan | null } catch { stored = null }
const plan = ref(stored)
const mapFailed = ref(false)
const lastLabel = computed(() => plan.value ? String.fromCharCode(64 + plan.value.stops.length) : '')
function legForStop(index: number): WalkLeg | undefined {
  if (!plan.value) return undefined
  const hasStartLeg = plan.value.legs.length === plan.value.stops.length
  return plan.value.legs[hasStartLeg ? index : index - 1]
}
function legText(leg: WalkLeg | undefined): string {
  if (!leg || leg.distance_m === null || leg.duration_min === null) return '步行路线暂不可用'
  return Math.round(leg.distance_m / 10) * 10 + ' 米 / 约 ' + leg.duration_min + ' 分钟'
}
</script>

<style scoped>
.page { max-width: 1190px; margin: 0 auto; padding: 24px 22px 70px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; color: #8b9b8e; font-size: 12px; letter-spacing: .07em; margin-bottom: 18px; }
.toolbar a { color: #247b5d; font-size: 14px; letter-spacing: 0; }
.hero { height: 340px; position: relative; overflow: hidden; border-radius: 22px; background: linear-gradient(120deg, #194238, #457d5f); color: #fff; }
.cover-image, .cover-shade { position: absolute; width: 100%; height: 100%; inset: 0; }
.cover-image { object-fit: cover; }
.cover-shade { background: linear-gradient(90deg, #102d29dd 5%, #102d298c 58%, #102d2925); }
.hero-content { position: absolute; z-index: 1; left: 5%; top: 16%; max-width: 640px; }
.eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .18em; color: #b6e4ca; }
.eyebrow.dark { color: #398564; }
h1 { font-size: clamp(30px, 4.5vw, 51px); line-height: 1.2; margin: 15px 0; }
.hero p { font-size: 15px; color: #e8f1e9; line-height: 1.7; }
.pills { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }
.pills span { border: 1px solid #ffffff70; background: #ffffff18; padding: 6px 11px; border-radius: 20px; font-size: 12px; }
.credit { position: absolute; z-index: 2; bottom: 12px; right: 16px; font-size: 11px; color: #fff; background: #142e2bb0; padding: 5px 9px; border-radius: 5px; }
.credit a { color: #fff; text-decoration: underline; }
.layout { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(270px, .8fr); gap: 20px; margin-top: 22px; }
.card { border-radius: 16px; box-shadow: 0 8px 24px #1c3c2910; margin-bottom: 20px; }
h2 { font-size: 21px; margin: 6px 0 16px; }
.section-top { display: flex; align-items: center; justify-content: space-between; }
.muted { color: #85998c; font-size: 12px; }
.stop-row { display: flex; gap: 18px; }
.rail { display: flex; flex-direction: column; align-items: center; flex: none; width: 34px; }
.number { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; background: #277b5b; color: #fff; font-weight: 700; flex: none; }
.line { width: 2px; background: #cfe3d4; flex: 1; min-height: 130px; }
.stop-content { padding: 0 0 22px; border-bottom: 1px solid #e9eee8; width: 100%; margin-bottom: 20px; }
.stop-row:last-child .stop-content { border: 0; margin-bottom: 0; }
.leg { color: #5a8b72; background: #edf6ec; padding: 7px 10px; border-radius: 6px; margin-bottom: 11px; font-size: 12px; }
.stop-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.stop-heading h3 { font-size: 19px; margin: 0; }
.stop-heading span, .category { color: #7a9080; font-size: 12px; }
.category { margin: 5px 0 9px; }
.stop-content p { line-height: 1.65; margin: 7px 0; }
.address { color: #667a6c; font-size: 13px; }
.open-map { display: inline-block; color: #20825a; margin-top: 10px; font-size: 13px; }
.map-image { width: 100%; display: block; border-radius: 9px; }
.map-note { color: #91a096; font-size: 12px; margin: 12px 0 0; }
.weather-card { background: #eaf4ec; }
.weather-main { font-size: 23px; color: #285e48; margin: 10px 0; }
.weather-main span { float: right; }
.side-column p, .side-column li { line-height: 1.7; }
.side-column ul { padding-left: 19px; margin: 0; }
.side-column li { margin: 9px 0; }
@media(max-width: 850px) { .layout { grid-template-columns: 1fr; } }
@media(max-width: 600px) { .page { padding: 14px 12px 50px; } .hero { height: 370px; } .hero-content { left: 6%; right: 6%; top: 13%; } .credit { left: 15px; right: auto; } .section-top { display: block; } }
</style>
