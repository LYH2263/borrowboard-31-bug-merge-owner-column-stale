<template>
  <div style="padding:16px;max-width:420px">
    <h1>上架</h1>
    <input v-model="title" placeholder="物品名" />
    <input v-model="owner" list="owner-names" placeholder="物主（可从现有户选或填新户名）" />
    <datalist id="owner-names">
      <option v-for="n in owners" :key="n" :value="n"></option>
    </datalist>
    <button @click="go">上架</button>
    <div v-if="error" class="error">{{ error }}</div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
const router = useRouter()
const title = ref('')
const owner = ref('')
const owners = ref([])
const error = ref('')
onMounted(async () => { owners.value = (await api('/owners')).owners.map(o => o.name) })
async function go() {
  error.value = ''
  try {
    await api('/items', { method: 'POST', body: JSON.stringify({ title: title.value, owner: owner.value }) })
    router.push('/')
  } catch (e) {
    if (e.code === 'name_merged') {
      error.value = `「${owner.value.trim()}」已并入「${e.canonical}」，请改用新户名上架。`
      owner.value = e.canonical
    } else {
      error.value = e.message || '上架失败。'
    }
  }
}
</script>
