<template>
  <a-layout class="app-shell">
    <a-layout-header class="topbar">
      <router-link :to="{name:ROUTE_NAMES.HOME}" class="brand"><span class="brand-mark">◎</span> TravelOS 城市探索</router-link>
      <nav v-if="currentUser" class="account-nav">
        <router-link :to="{name:ROUTE_NAMES.HOME}">开始规划</router-link>
        <router-link :to="{name:ROUTE_NAMES.MY_TRIPS}">我的规划</router-link>
        <span>{{ currentUser.username }}</span>
        <a-button size="small" :loading="loggingOut" @click="signOut">退出</a-button>
      </nav>
      <span v-else class="topbar-note">城市漫步 · 景点游览 · 餐厅推荐</span>
    </a-layout-header>
    <a-layout-content><router-view /></a-layout-content>
    <a-layout-footer class="footer">真实地点与路线来自高德 · Jev 辅助选择</a-layout-footer>
  </a-layout>
</template>

<script setup lang="ts">
import { ROUTE_NAMES } from '@/config/routes'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { showError } from '@/api/error-dialog'
import { currentUser } from '@/api/auth-state'
import { logout } from '@/api/auth'
const router = useRouter(), loggingOut = ref(false)
async function signOut() {
  loggingOut.value = true
  try { await logout(); await router.replace({name:ROUTE_NAMES.LOGIN}) }
  catch (error) { showError(error instanceof Error ? error.message : '退出失败，请稍后重试') }
  finally { loggingOut.value = false }
}
</script>

<style>
.account-nav { display:flex; align-items:center; gap:16px; color:#bad7ca; }
.account-nav a { color:#fff; }
@media(max-width:650px) { .account-nav { gap:8px; font-size:12px; } .account-nav > span { display:none; } .brand { font-size:15px!important; } }
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
