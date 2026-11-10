<template>
  <section class="inbox">
    <header>
      <div>
        <h1>
          待我处理 <span>{{ items.length }}</span>
        </h1>
      </div>
      <button type="button" @click="load">刷新</button>
    </header>
    <div class="filters">
      <label
        >类型<select v-model="filter">
          <option value="">全部类型</option>
          <option v-for="(name, k) in kinds" :value="k" :key="k">
            {{ name }}
          </option>
        </select></label
      >
      <label
        >员工<input v-model.trim="search" placeholder="姓名或工号"
      /></label>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="!loaded && !error">正在读取待办…</p>
    <p v-if="loaded && !visible.length">
      {{ items.length ? '没有符合筛选条件的待办。' : '当前没有待处理事项。' }}
    </p>
    <article v-for="t in visible" :key="t.key">
      <header>
        <strong
          >{{ t.name }} <small>{{ t.number }}</small></strong
        ><span>{{ t.title }}</span>
      </header>
      <p>
        {{ t.day || fmt(t.since) }}
        <template v-if="t.node"> · 当前节点 {{ t.node }}</template>
      </p>
      <p v-if="t.reason">{{ t.reason }}</p>
      <p v-if="t.balance != null">
        当前余额 <b>{{ t.balance }} 天</b> → 批准后
        <b>{{ t.balance - 1 }} 天</b>
      </p>
      <p v-if="t.kind === 'hours'">
        {{ t.shift === 'night' ? '夜班（晚班）' : '白班（早班）' }} · 应满 12
        小时 · 实际 {{ duration(t.worked_seconds) }} ·
        {{
          t.suggested == null
            ? '差额待核定'
            : '建议缺 ' + duration(t.suggested * 60)
        }}
      </p>
      <p v-if="t.period">{{ t.period }}</p>
      <button v-if="selected !== t.key" type="button" @click="open(t)">
        查看并处理
      </button>
      <form v-else @submit.prevent="submit(t)">
        <p v-for="p in t.punches" :key="p.at + p.kind">
          {{ p.kind === 'in' ? '上班' : '下班' }} {{ fmt(p.at) }} ·
          {{ p.projects.join(' / ') }}
        </p>
        <p v-for="f in t.fields" :key="f.label">{{ f.label }}　{{ f.value }}</p>
        <label v-if="t.kind === 'project'"
          >所属项目<select required v-model="choice">
            <option disabled value="">请选择</option>
            <option v-for="(name, code) in t.choices" :key="code" :value="code">
              {{ name }}（{{ code }}）
            </option>
          </select></label
        >
        <template v-else>
          <label
            >审批结果<select required v-model="choice">
              <option disabled value="">请选择</option>
              <template v-if="t.kind === 'hours'"
                ><option value="normal">认定正常</option>
                <option value="shortage">确认缺工时</option></template
              >
              <template v-else
                ><option value="approve">批准</option>
                <option value="reject">驳回</option></template
              >
            </select></label
          >
          <label v-if="choice === 'shortage'"
            >核定缺少分钟<input
              v-model.number="missing"
              required
              type="number"
              min="1"
              max="720"
              step="1"
          /></label>
          <label
            >{{ t.kind === 'hours' ? '审批原因' : '审批意见'
            }}<input
              v-model.trim="note"
              :required="t.kind === 'hours' || (t.kind === 'correction' && choice === 'reject')"
              maxlength="500"
          /></label>
        </template>
        <div>
          <button class="primary" :disabled="busy || !choice">
            {{ t.kind === 'project' ? '确认项目' : '提交审批' }}
          </button>
          <button type="button" :disabled="busy" @click="selected = ''">
            收起
          </button>
        </div>
      </form>
    </article>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
interface Task {
  key: string
  id: number
  kind: string
  name: string
  number: string
  since: string
  title: string
  day?: string
  reason?: string
  node?: string
  order?: number
  period?: string
  balance?: number
  shift?: string
  worked_seconds?: number
  suggested?: number | null
  choices?: Record<string, string>
  fields?: { label: string; value: string }[]
  punches?: { at: string; kind: string; projects: string[] }[]
}
const props = defineProps<{
  get: (path: string) => Promise<any>
  post: (path: string, body: Record<string, unknown>) => Promise<any>
}>()
const kinds = {
  rest: '额度休假与补休',
  correction: '补卡申请',
  hours: '工时异常',
  project: '项目确认',
  leave: '独立请假',
  flow: '业务流程',
}
const items = ref<Task[]>([]),
  filter = ref(''),
  search = ref(''),
  selected = ref(''),
  choice = ref(''),
  missing = ref<number | null>(null),
  note = ref('')
const notice = ref(''),
  error = ref(''),
  busy = ref(false),
  loaded = ref(false)
const visible = computed(() =>
  items.value.filter(
    (t) =>
      (!filter.value || filter.value === t.kind) &&
      (t.name + t.number).includes(search.value),
  ),
)
const fmt = (s: string) => s.replace('T', ' ').slice(0, 16)
const duration = (seconds = 0) =>
  Math.floor(seconds / 3600) +
  '小时' +
  (Math.floor(seconds / 60) % 60 ? (Math.floor(seconds / 60) % 60) + '分' : '')
let revision = 0,
  timer: ReturnType<typeof setInterval>
async function load() {
  const rev = ++revision
  try {
    const data = await props.get('/monthly/manager-inbox')
    if (rev !== revision) return
    items.value = data.items
    loaded.value = true
    if (selected.value && !items.value.some((t) => t.key === selected.value)) {
      selected.value = ''
      notice.value = '该事项已处理或已更新。'
    }
    window.dispatchEvent(
      new CustomEvent('manager-inbox-count', { detail: data.total }),
    )
  } catch (e: any) {
    if (rev === revision)
      error.value = e.response?.data?.detail || '待办读取失败，请刷新。'
  }
}
function open(t: Task) {
  selected.value = t.key
  choice.value = ''
  missing.value = t.suggested ?? null
  note.value = ''
  notice.value = ''
  error.value = ''
}
async function submit(t: Task) {
  if (busy.value || selected.value !== t.key) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const approved = choice.value === 'approve'
    let result
    switch (t.kind) {
      case 'correction':
        result = await props.post('/monthly/corrections/' + t.id + '/review', {approved, note:note.value})
        break
      case 'rest':
        result = await props.post('/monthly/requests/' + t.id + '/review', {
          approved,
          note: note.value,
        })
        break
      case 'hours':
        result = await props.post('/monthly/hours/review', {
          punch_id: t.id,
          outcome: choice.value,
          missing_minutes: choice.value === 'shortage' ? missing.value : 0,
          reason: note.value,
        })
        break
      case 'project':
        result = await props.post('/monthly/confirm', {
          punch_id: t.id,
          code: choice.value,
        })
        break
      case 'leave':
        result = await props.post('/leave/requests/' + t.id + '/approve', {
          approved,
          note: note.value,
        })
        break
      case 'flow':
        result = await props.post('/approval/instances/' + t.id + '/process', {
          action: choice.value,
          comment: note.value,
          expected_node_order: t.order,
        })
        break
      default:
        throw Error('未知事项类型')
    }
    selected.value = ''
    notice.value = result?.already_processed
      ? '该事项已处理。'
      : '已提交处理结果。'
    await load()
  } catch (e: any) {
    const message = e.response?.data?.detail || '提交失败，请重试。'
    await load()
    if (items.value.some((x) => x.key === t.key)) error.value = message
    else notice.value = '该事项已处理或已更新。'
  } finally {
    busy.value = false
  }
}
function refreshVisible() {
  if (!document.hidden && !busy.value) void load()
}
onMounted(() => {
  void load()
  timer = setInterval(refreshVisible, 30000)
  window.addEventListener('focus', refreshVisible)
  document.addEventListener('visibilitychange', refreshVisible)
})
onBeforeUnmount(() => {
  revision++
  clearInterval(timer)
  window.removeEventListener('focus', refreshVisible)
  document.removeEventListener('visibilitychange', refreshVisible)
})
</script>
<style scoped>
.inbox {
  max-width: 900px;
  margin: auto;
  padding: 18px;
  color: #23394b;
  background: white;
  border-radius: 12px;
}
.inbox header,
.filters {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
h1 {
  font-size: 24px;
  margin: 4px 0;
}
h1 span {
  font-size: 17px;
  color: #075768;
}
.filters {
  justify-content: flex-start;
  margin: 18px 0;
}
label {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
input,
select,
button {
  font: inherit;
  font-size: 14px;
  background: white;
  border: 1px solid #abbcc6;
  border-radius: 6px;
  padding: 8px;
}
button {
  cursor: pointer;
  color: #124e61;
}
.primary {
  background: #145a6b;
  color: white;
}
button:disabled {
  opacity: 0.55;
}
article {
  border: 1px solid #dce4e8;
  border-radius: 8px;
  padding: 14px;
  margin: 12px 0;
}
small {
  font-weight: normal;
  color: #536473;
}
form {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}
p {
  overflow-wrap: anywhere;
}
p[role='alert'] {
  color: #a32027;
}
</style>
