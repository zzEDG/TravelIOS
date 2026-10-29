import { ref, watch } from 'vue'
function savedModel() {
  try { return localStorage.getItem('travel-ai-model') || 'default' } catch { return 'default' }
}
export const selectedModel = ref(savedModel())
export const modelWarning = ref('')
watch(selectedModel, value => {
  modelWarning.value = ''
  try { localStorage.setItem('travel-ai-model', value) } catch { /* Storage is optional. */ }
})
