<template>
  <div style="padding:16px;max-width:640px">
    <h1>物主一览</h1>
    <div class="muted" style="margin-bottom:12px">在册户数：<strong>{{ data.count || 0 }}</strong>（无主脏数据不计）</div>

    <div class="merge-form item">
      <h2 style="margin:0 0 8px;font-size:16px">合并户名</h2>
      <label>被并户（旧名）</label>
      <select v-model="form.old_name">
        <option value="" disabled>请选择…</option>
        <option v-for="o in data.owners" :key="o.name" :value="o.name">
          {{ o.name }}<template v-if="o.absorbed.length">（含 {{ o.absorbed.join('、') }}）</template>
        </option>
      </select>
      <label>并入户</label>
      <select v-model="targetMode" @change="onTargetMode">
        <option value="existing">选择现有户</option>
        <option value="new">新户名</option>
      </select>
      <select v-if="targetMode==='existing'" v-model="form.new_name">
        <option value="" disabled>请选择…</option>
        <option v-for="o in targetOptions" :key="o.name" :value="o.name">{{ o.name }}</option>
      </select>
      <input v-else v-model="form.new_name" placeholder="新户名，如 李四家" />
      <label>在借行署名规则</label>
      <div class="radio-row">
        <label><input type="radio" value="keep" v-model="form.attribution" /> 保留旧物主字</label>
        <label><input type="radio" value="rewrite" v-model="form.attribution" /> 改写成新户名</label>
      </div>
      <button @click="merge" :disabled="busy">{{ busy ? '合并中…' : '合并' }}</button>
      <div v-if="error" class="error">{{ error }}</div>
      <div v-if="okMsg" class="ok">{{ okMsg }}</div>
    </div>

    <div v-for="o in data.owners" :key="o.name" class="item">
      <strong>{{ o.name }}</strong>
      <span v-if="o.absorbed.length" class="alias">（旧名：{{ o.absorbed.join('、') }}）</span>
      <div class="muted">可借 {{ o.available_count }} · 在借 {{ o.on_loan_count }}</div>
    </div>

    <div v-if="ownerless.length" class="item dirty">
      <strong>无主（脏数据，不可合并、不计户数）</strong>
      <div v-for="i in ownerless" :key="i.id" class="muted">{{ i.title }} · {{ i.status }}</div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, inject, onMounted } from 'vue'
import { api } from '../api'
const reloadBoard = inject('reloadBoard')
const data = ref({ count: 0, owners: [] })
const ownerless = ref([])
const busy = ref(false)
const error = ref('')
const okMsg = ref('')
const targetMode = ref('existing')
const form = ref({ old_name: '', new_name: '', attribution: 'keep' })

const targetOptions = computed(() => data.value.owners.filter(o => o.name !== form.value.old_name))
function onTargetMode() { form.value.new_name = '' }

async function refresh() {
  data.value = await api('/owners')
  const items = await api('/items')
  ownerless.value = items.filter(i => !i.owner)
}
async function merge() {
  error.value = ''; okMsg.value = ''
  if (!form.value.old_name || !form.value.new_name.trim()) {
    error.value = '请选择被并户并指定并入户。'
    return
  }
  busy.value = true
  try {
    const r = await api('/owners/merge', {
      method: 'POST',
      body: JSON.stringify({ ...form.value, new_name: form.value.new_name.trim() }),
    })
    okMsg.value = `已将「${r.absorbed}」并入「${r.canonical}」。`
    form.value.old_name = ''; form.value.new_name = ''
    await refresh()
    await reloadBoard()
  } catch (e) {
    if (e.code === 'already_merged') error.value = `该户名此前已并入「${e.canonical}」。`
    else if (e.code === 'canonical_is_absorbed') error.value = `目标户名「${e.canonical}」已是旧名，不能并入。`
    else if (e.code === 'old_household_not_found') error.value = '被并户名下已无物品，无法合并。'
    else if (e.code === 'same_owner' || e.code === 'empty_owner') error.value = '被并户与并入户必须是两个非空户名。'
    else error.value = e.message || '合并失败，数据未改动。'
  } finally {
    busy.value = false
  }
}
onMounted(refresh)
</script>
