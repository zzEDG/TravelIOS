<template>
  <a-layout class="app-shell">
    <a-layout-header class="topbar">
      <router-link :to="{name:ROUTE_NAMES.HOME}" class="brand"><span class="brand-mark">◎</span> TravelOS 城市探索</router-link>
      <span class="topbar-note">城市漫步 · 景点游览 · 餐厅推荐</span>
    </a-layout-header>
    <a-layout-content><router-view /></a-layout-content>
    <a-layout-footer class="footer">真实地点与路线来自高德 · Jev 辅助选择</a-layout-footer>
  </a-layout>
</template>

<script setup lang="ts">
import { ROUTE_NAMES } from '@/config/routes'
</script>

<style>
.travel-page { max-width: 1160px; margin: 0 auto; padding: 24px 22px 60px; }
.travel-card { border-radius: 16px; margin-bottom: 20px; box-shadow: 0 8px 24px #1c3c2910; }
.notice { margin: 18px 0; }
.muted { color: #71867b; font-size: 13px; line-height: 1.7; }
.eyebrow { font-size: 12px; letter-spacing: .08em; color: #71b294; }
body { margin: 0; background: #f5f5f0; color: #1f302e; }
.app-shell { min-height: 100vh; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 0 5vw; background: #173b35; }
.brand { color: #fff; font-size: 19px; font-weight: 700; letter-spacing: .04em; }
.brand:hover { color: #bcebd8; }
.brand-mark { font-size: 25px; margin-right: 8px; vertical-align: -1px; }
.topbar-note { color: #bad7ca; font-size: 13px; }
.footer { text-align: center; color: #82958c; background: transparent; }
@media (max-width: 650px) { .topbar-note { display: none; } .topbar { padding: 0 18px; } }
</style>
