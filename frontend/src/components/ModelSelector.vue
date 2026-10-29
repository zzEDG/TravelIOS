<template>
  <a-form-item label="AI 模型">
    <a-select v-model:value="selectedModel" :options="options" :disabled="disabled || loading" style="width:100%" />
    <p class="muted">切换后用于下一次规划或对话，已填写内容会保留。</p>
    <a-alert v-if="modelWarning" type="warning" show-icon :message="modelWarning" />
    <a-alert v-if="loadError" type="warning" :message="loadError" show-icon><template #description><a-button size="small" @click="load">重试加载模型</a-button></template></a-alert>
  </a-form-item>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { request } from '@/api/http'
import { selectedModel, modelWarning } from '@/api/model'
defineProps<{disabled?: boolean}>()
const options=ref([{value:'default',label:'环境默认模型',disabled:false}])
const loading=ref(false), loadError=ref('')
async function load() {
  loading.value=true; loadError.value=''
  try {
    const response=await request<{data: {value:string;label:string;available:boolean}[]}>('/api/v1/models')
    options.value=response.data.map(m=>({value:m.value,label:m.label+(m.available?'':'（未配置）'),disabled:!m.available}))
    if(!options.value.some(m=>m.value===selectedModel.value && !m.disabled)) selectedModel.value='default'
  } catch { loadError.value='模型列表加载失败，请确认后端连接后重试。' }
  finally { loading.value=false }
}
onMounted(load)
</script>
