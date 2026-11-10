<template>
  <div class="correction">
    <p v-for="c in day.corrections" :key="c.id">
      补卡申请 · {{ states[c.status] }} · {{ label(c.shift) }}<br>
      <span v-if="c.check_in">补上班 {{ format(c.check_in) }}　</span>
      <span v-if="c.check_out">补下班 {{ format(c.check_out) }}</span><br>
      {{ c.original.length ? '打卡项目' : '员工申报项目' }} · {{ c.projects.map((p: Project) => p.name).join(' / ') }}<br>
      {{ c.reason }}<br>
      <small>申请于 {{ format(c.created) }}</small>
      <small v-if="c.reviewed"> · 审批于 {{ format(c.reviewed) }} · {{ c.note || states[c.status] }}</small>
    </p>
    <button v-if="day.can_correct && !editing" type="button" @click="open">申请补卡</button>
    <form v-if="editing" @submit.prevent="submit">
      <strong>{{ day.day }} · {{ slotsTitle }}</strong>
      <template v-if="!day.punches.length">
        <label>班次<select required v-model="shift"><option value="" disabled>请选择</option><option value="day">白班</option><option value="night">夜班</option></select></label>
        <label>员工申报项目<select required v-model="project"><option value="" disabled>请选择</option><option v-for="p in projects" :key="p.code" :value="p.code">{{ p.name }}（{{ p.code }}）</option></select></label>
      </template>
      <p v-else>{{ label(day.shift) }} · {{ day.projects.map((p: Project) => p.name).join(' / ') }}</p>
      <label v-if="day.missing_slots.includes('in')">实际上班时间（北京时间）<input type="datetime-local" required v-model="checkIn"></label>
      <label v-if="day.missing_slots.includes('out')">实际下班时间（北京时间）<input type="datetime-local" required v-model="checkOut"></label>
      <label>补卡原因<textarea required maxlength="500" v-model.trim="reason"></textarea></label>
      <div><button type="submit" :disabled="busy">提交售后经理审批</button> <button type="button" :disabled="busy" @click="editing = false">取消</button></div>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { get, post } from '@/utils/request'
interface Project { code: string; name: string }
const props = defineProps<{ day: any }>()
const emit = defineEmits<{ submitted: [] }>()
const states: Record<string,string> = {pending:'待审批',approved:'已批准',rejected:'已驳回'}
const label = (value: string) => value === 'night' ? '夜班' : '白班'
const format = (s: string) => s.replace('T',' ').slice(0,16)
const slotsTitle = computed(() => props.day.missing_slots.map((s:string) => s === 'in' ? '补上班卡' : '补下班卡').join('、'))
const editing=ref(false), busy=ref(false), error=ref(''), projects=ref<Project[]>([])
const shift=ref(''), project=ref(''), checkIn=ref(''), checkOut=ref(''), reason=ref('')
async function open() {
  error.value=''
  try {
    if (!props.day.punches.length) projects.value=await get('/monthly/me/correction-projects')
    shift.value=props.day.shift || ''
    editing.value=true
  } catch(e:any) { error.value=e.response?.data?.detail || '无法读取项目，请重试' }
}
async function submit() {
  if (busy.value) return
  busy.value=true
  error.value=''
  try {
    await post('/monthly/me/corrections',{day:props.day.day, shift:shift.value,
      project_code:project.value || null, check_in:checkIn.value || null,
      check_out:checkOut.value || null, reason:reason.value})
    editing.value=false
    emit('submitted')
  } catch(e:any) { error.value=e.response?.data?.detail || '补卡申请提交失败' }
  finally { busy.value=false }
}
</script>
<style scoped>
form,label{display:grid;gap:.4rem} form{gap:.8rem;padding-top:.5rem}
input,select,textarea,button{padding:.5rem;border:1px solid #adbfc9;border-radius:6px;background:white;max-width:100%;box-sizing:border-box}
[role=alert]{color:#a02127}
</style>
