<template>
  <main class="travel-page">
    <h1>我的规划</h1>
    <p class="muted">查看历史版本，或继续编辑你的行程。</p>
    <PlanHistory />
  </main>
</template>
<script setup lang="ts">
import PlanHistory from '@/components/PlanHistory.vue'
</script>
