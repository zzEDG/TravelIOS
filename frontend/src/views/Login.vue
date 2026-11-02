<template>
  <main class="auth-page">
    <section class="auth-intro">
      <div class="eyebrow">TRAVELOS · 城市探索</div>
      <h1>下一段城市故事，<br />从这里开始。</h1>
      <p>保存你的漫步路线、景点与餐厅推荐。<br />每次登录，都能继续自己的规划。</p>
      <div class="auth-tags">城市漫步 · 景点游览 · 餐厅推荐</div>
    </section>
    <a-card class="auth-card">
      <h2>{{ registering ? '创建你的账号' : '欢迎回来' }}</h2>
      <p class="muted">{{ registering ? '注册成功后即可登录，开始第一份规划。' : '登录后查看并继续你的规划。' }}</p>
      <a-alert v-if="error" :message="error" type="error" show-icon class="notice" />
      <a-alert v-if="success" :message="success" type="success" show-icon class="notice" />
      <a-alert v-if="route.query.reason === 'network'" message="暂时无法连接后端，请确认服务已启动后重试。" type="warning" class="notice" />
      <form @submit.prevent="submit">
        <label for="auth-username">用户名</label>
        <a-input id="auth-username" v-model:value="username" autocomplete="username" :maxlength="32" placeholder="3–32 位字母、数字或下划线" :disabled="busy" />
        <label for="auth-password">密码</label>
        <a-input-password id="auth-password" v-model:value="password" :autocomplete="registering ? 'new-password' : 'current-password'" :maxlength="128" placeholder="12–128 个字符" :disabled="busy" />
        <template v-if="registering">
          <label for="auth-confirm">确认密码</label>
          <a-input-password id="auth-confirm" v-model:value="confirm" autocomplete="new-password" :maxlength="128" :disabled="busy" />
        </template>
        <a-button type="primary" html-type="submit" block size="large" :loading="busy" class="auth-submit">{{ registering ? '注册账号' : '登录' }}</a-button>
      </form>
      <a-button type="link" :disabled="busy" @click="toggle">{{ registering ? '已有账号？返回登录' : '还没有账号？立即注册' }}</a-button>
    </a-card>
  </main>
</template>
<script setup lang="ts">
import { showError } from '@/api/error-dialog'
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login, register } from '@/api/auth'
const route = useRoute(), router = useRouter()
const username = ref(''), password = ref(''), confirm = ref('')
const registering = ref(false), busy = ref(false), error = ref(''), success = ref('')
watch(error, value => { if (value) showError(value) })
function toggle() { registering.value = !registering.value; password.value = ''; confirm.value = ''; error.value = ''; success.value = '' }
async function submit() {
  if (busy.value) return
  error.value = ''; success.value = ''
  if (registering.value && password.value !== confirm.value) { error.value = '两次输入的密码不一致'; return }
  busy.value = true
  try {
    if (registering.value) {
      await register(username.value, password.value)
      registering.value = false; password.value = ''; confirm.value = ''
      success.value = '注册成功，请登录。'
    } else {
      await login(username.value, password.value)
      password.value = ''
      const next = typeof route.query.next === 'string' ? route.query.next : '/'
      await router.replace(next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') && !next.startsWith('/login') ? next : '/')
    }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请稍后重试' }
  finally { busy.value = false }
}
</script>
<style scoped>
.auth-page { max-width: 1040px; margin: 60px auto; padding: 24px; display: grid; grid-template-columns: 1.1fr 1fr; gap: 70px; align-items: center; }
.auth-intro h1 { color: #173b35; font-size: 38px; line-height: 1.5; }
.auth-intro p { color: #71867b; line-height: 2; }
.auth-tags { margin-top: 30px; color: #316b58; }
.auth-card { border-radius: 18px; box-shadow: 0 12px 40px #173b3510; }
label { display: block; margin: 20px 0 8px; }
.auth-submit { margin: 28px 0 14px; background: #173b35; }
@media(max-width: 700px) { .auth-page { display: block; margin: 12px auto; } .auth-intro { margin-bottom: 28px; } .auth-intro h1 { font-size: 28px; } .auth-tags { display: none; } }
</style>
