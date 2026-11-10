<template>
  <section class="monthly">
    <header>
      <h1>员工月度考勤</h1>
      <button @click="load">刷新</button>
    </header>
    <nav>
      <button
        v-for="t in tabs"
        :key="t"
        :aria-pressed="tab === t"
        @click="tab = t"
      >
        {{ t }}</button
      ><router-link to="/approval">业务审批</router-link>
    </nav>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="!ctx.active">请设置统一考勤规则。从生效日起按满勤天数累计休假。</p>
    <template v-if="tab === '每日考勤'">
      <div class="filters">
        <label
          >考勤日期 <input type="date" v-model="date" @change="load" /></label
        ><label
          >员工 <input v-model.trim="search" placeholder="姓名或工号" /></label
        ><label
          >状态
          <select v-model="status">
            <option value="">全部</option>
            <option v-for="(label, key) in labels" :key="key" :value="key">
              {{ label }}
            </option>
          </select></label
        >
      </div>
      <table>
        <thead>
          <tr>
            <th>员工</th>
            <th>当日状态</th>
            <th>班次 / 实际工时</th>
            <th>项目 / 上下班卡</th>
            <th>本月打卡 / 满勤</th>
            <th>已休 / 休假余额</th>
            <th>缺卡 / 缺勤</th>
            <th>本月缺工时</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in visibleRows" :key="r.employee_id">
            <td>
              {{ r.name }}<small>{{ r.employee_no }}</small>
            </td>
            <td>
              <span class="pill" :class="r.day.status">{{
                labels[r.day.status]
              }}</span
              ><small
                >{{ r.day.anomalies.join('、') }}
                {{ requestText(r.day) }}
                <template
                  v-if="
                    r.day.hours_status === 'pending_hours' &&
                    r.day.suggested_minutes != null
                  "
                >
                  · 建议缺 {{ duration(r.day.suggested_minutes) }}</template
                >
                <template v-if="r.day.review?.missing_minutes">
                  · 已确认缺
                  {{ duration(r.day.review.missing_minutes) }}</template
                >
                <template
                  v-if="
                    r.day.status !== 'conflict' &&
                    !r.day.project &&
                    r.day.projects.length > 1
                  "
                >
                  · 项目待确认</template
                ></small
              >
            </td>
            <td>
              {{ shiftLabel(r.day.shift)
              }}<small v-if="r.day.shift">{{
                duration(Math.floor(r.day.worked_seconds / 60))
              }}</small>
            </td>
            <td :class="{ projectGreen: !!r.day.project }">
              {{
                r.day.project?.name ||
                r.day.projects.map((p: any) => p.name).join(' / ') ||
                '—'
              }}<small>{{
                r.day.punches
                  .map(
                    (p: any) =>
                      (p.kind === 'in' ? '上 ' : '下 ') +
                      p.at.slice(5, 16).replace('T', ' '),
                  )
                  .join(' · ')
              }}</small>
            </td>
            <td>
              {{ r.punch_count }} 次<small>审批补卡 {{ r.corrected_count }} 次</small><small>满勤 {{ r.full_days }} 天</small>
            </td>
            <td>
              已休 {{ r.leave_days }} 天<small v-if="r.earned_rest"
                >余额 {{ r.rest_balance }} 天 · 进度
                {{ r.work_progress }}/6</small
              >
            </td>
            <td>
              缺卡 {{ r.missing_punches }} 次<small
                >缺勤 {{ r.absence_days }} 天</small
              >
            </td>
            <td>
              已确认 {{ duration(r.shortage_minutes)
              }}<small>{{ r.shortage_days }} 天</small>
              <small v-if="r.pending_hours_days"
                >待审 {{ r.pending_hours_days }} 天 · 可核算
                {{ duration(r.pending_estimate_minutes)
                }}<template v-if="r.pending_unknown_days">
                  · {{ r.pending_unknown_days }} 天待核定</template
                ></small
              >
            </td>
            <td>
              <button
                v-if="r.day.hours_status === 'pending_hours'"
                @click="openReview(r, r.day)"
              >
                审批工时</button
              ><button @click="selected = r.employee_id">月明细</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!visibleRows.length">所选日期没有匹配员工。</p>
      <article v-if="detail">
        <header>
          <h2>{{ detail.name }} · {{ date.slice(0, 7) }}</h2>
          <button @click="selected = 0">收起</button>
        </header>
        <p>
          本月满勤 {{ detail.full_days }} 天 · 已确认缺工时
          {{ detail.shortage_days }} 天，共
          {{ duration(detail.shortage_minutes) }}
        </p>
        <div v-for="d in [...detail.days].reverse()" :key="d.day" class="entry">
          <strong>{{ d.day }}</strong>
          <span class="pill" :class="d.status">{{ labels[d.status] }}</span>
          {{ d.project?.name || d.holiday }} {{ d.anomalies.join('、') }}
          {{ requestText(d) }}
          <small v-if="d.shift"
            >{{ shiftLabel(d.shift) }} · 实际
            {{ duration(Math.floor(d.worked_seconds / 60)) }} ·
            {{ labels[d.hours_status] }}</small
          >
          <small v-if="d.shift"
            >要求 12 小时 ·
            {{
              d.suggested_minutes == null
                ? '缺卡，差额待核定'
                : '打卡差额 ' + duration(d.suggested_minutes)
            }}</small
          >
          <small v-if="d.review"
            >{{ d.review.reason }} · 核定缺
            {{ duration(d.review.missing_minutes) }} ·
            {{ fmt(d.review.at) }}</small
          >
          <button
            v-if="d.hours_status === 'pending_hours'"
            @click="openReview(detail, d)"
          >
            审批工时
          </button>
          <small v-if="d.status === 'absent'"
            >缺勤时段 {{ fmt(d.start) }} 至 {{ fmt(d.end) }}</small
          >
          <small v-for="p in d.punches" :key="p.id"
            >{{ p.corrected ? '审批补卡 · ' : '' }}{{ p.kind === 'in' ? '上班' : '下班' }} {{ fmt(p.at) }} ·
            {{ p.matches.map((m: any) => m.name).join(' / ') }}
            <a
              v-if="p.photo"
              :href="buildProtectedFileUrl(p.photo)"
              target="_blank"
              >照片</a
            ></small
          >
          <small v-for="c in d.corrections" :key="c.id">补卡{{ ({pending:'待审批',approved:'已批准',rejected:'已驳回'} as Record<string,string>)[c.status] }} · {{ c.reason }} {{ c.note }}</small>
          <small v-for="h in d.prior_hours_reviews" :key="h.at">补卡前的工时审批 · {{ labels[h.outcome] }} · {{ h.reason }}</small>
          <div v-if="!d.project && d.projects.length > 1">
            确认当日唯一项目
            <button
              v-for="p in d.projects"
              :key="p.code"
              :disabled="busy"
              @click="confirmDay(d, p.code)"
            >
              {{ p.name }}（{{ p.code }}）
            </button>
          </div>
        </div>
      </article>
    </template>
    <template v-if="tab === '工时审批'">
      <label
        >考勤月份
        <input type="month" :value="date.slice(0, 7)" @change="changeMonth"
      /></label>
      <p v-if="!pendingHours.length">本月没有待审批工时。</p>
      <article v-for="{ r, d } in pendingHours" :key="d.revision">
        {{ r.name }} · {{ d.day }} · {{ shiftLabel(d.shift) }} · 实际
        {{ duration(Math.floor(d.worked_seconds / 60)) }} ·
        {{ d.anomalies.join('、') }}
        <button @click="openReview(r, d)">审批工时</button>
      </article>
    </template>
    <dialog ref="reviewDialog" @close="reviewing = null">
      <form v-if="reviewing" @submit.prevent="submitHours">
        <h2>{{ reviewing.name }} · 工时审批</h2>
        <span
          >{{ reviewing.d.day }} · {{ shiftLabel(reviewing.d.shift) }} · 实际
          {{ duration(Math.floor(reviewing.d.worked_seconds / 60)) }}</span
        >
        <span
          >{{ reviewing.d.anomalies.join('、') }} ·
          {{
            reviewing.d.suggested_minutes == null
              ? '缺卡，请核定实际差额'
              : '建议缺 ' + duration(reviewing.d.suggested_minutes)
          }}</span
        >
        <label
          >审批结果
          <select v-model="outcome" required>
            <option disabled value="">请选择</option>
            <option value="normal">认定正常</option>
            <option value="shortage">确认缺工时</option>
          </select></label
        >
        <label v-if="outcome === 'shortage'"
          >核定缺工时（分钟）<input
            v-model.number="missing"
            type="number"
            min="1"
            max="720"
            step="1"
            required
        /></label>
        <label
          >审批原因 <input v-model.trim="reviewReason" required maxlength="500"
        /></label>
        <p v-if="error" role="alert">{{ error }}</p>
        <button :disabled="busy">确认审批</button
        ><button type="button" @click="reviewDialog?.close()">取消</button>
      </form>
    </dialog>
    <template v-if="tab === '休假审批'"
      ><p v-if="!requests.length">暂无休假申请。</p>
      <article v-for="r in requests" :key="r.id">
        <strong>{{ r.name }} · {{ r.day }}</strong
        >　{{ states[r.status] }}
        <p>{{ r.reason }}</p>
        <template v-if="r.status === 'pending'"
          ><input v-model="notes[r.id]" placeholder="审批意见" /><button
            :disabled="busy"
            @click="review(r, true)"
          >
            批准</button
          ><button :disabled="busy" @click="review(r, false)">
            驳回
          </button></template
        ><small v-else>{{ r.note }}</small>
      </article></template
    >
    <template v-if="tab === '统一规则'"
      ><form @submit.prevent="saveRule">
        <label
          >生效日期 <input type="date" required v-model="form.effective"
        /></label>
        <p>
          白班、夜班每日应出勤 12 小时。白班在当日结束时结算，夜班在首次上班后
          24 小时结算。每满勤 6 天获得 1 天休假，余额和进度跨月保留。
        </p>
        <label
          ><input type="checkbox" v-model="form.photo" /> 打卡必须拍照</label
        ><button :disabled="busy">保存规则</button>
      </form>
      <p v-for="p in ctx.policies" :key="p.effective">
        {{ p.effective }} 起 · {{ p.minutes / 60 }} 小时 ·
        {{ String(Math.floor(p.cutoff / 60)).padStart(2, '0') }}:{{
          String(p.cutoff % 60).padStart(2, '0')
        }}
        交界 · {{ p.photo ? '必须拍照' : '照片选填' }}
      </p></template
    >
    <template v-if="tab === '项目地点'"
      ><div class="filters">
        <input
          type="file"
          accept=".xlsx"
          aria-label="项目地点表"
          @change="pickFile"
        /><button :disabled="busy || !file" @click="importPlaces(false)">
          预览导入</button
        ><button
          v-if="preview"
          :disabled="busy || preview.summary.error > 0"
          @click="importPlaces(true)"
        >
          确认导入
        </button>
      </div>
      <details v-if="preview">
        <summary>
          预览 {{ preview.summary.total }} 行，新增
          {{ preview.summary.create }}，更新 {{ preview.summary.update }}，错误
          {{ preview.summary.error }}
        </summary>
        <p v-for="r in preview.rows" :key="r.row_number || r.code">
          {{ r.code }} {{ r.name }} · {{ r.status }} {{ r.address }}
          {{ r.latitude }} {{ r.longitude }} {{ r.errors?.join('、') }}
        </p>
      </details>
      <label
        >查找项目 <input v-model="placeQuery" placeholder="项目编号或名称"
      /></label>
      <table>
        <thead>
          <tr>
            <th>项目</th>
            <th>打卡位置</th>
            <th>半径</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in filteredPlaces"
            :key="p.code + String(p.latitude) + String(p.longitude)"
          >
            <td>{{ p.code }} · {{ p.name }}</td>
            <td>
              {{
                p.latitude == null
                  ? '缺少坐标'
                  : p.address || p.latitude + ', ' + p.longitude
              }}
            </td>
            <td>{{ p.radius }} 米</td>
            <td><button @click="editing = { ...p }">设置</button></td>
          </tr>
        </tbody>
      </table>
      <dialog :open="!!editing" v-if="editing">
        <form @submit.prevent="savePlace">
          <h2>{{ editing.name }}</h2>
          <label>地址 <input v-model="editing.address" /></label
          ><label
            >腾讯地图纬度
            <input
              required
              type="number"
              step="any"
              min="-90"
              max="90"
              v-model.number="editing.latitude" /></label
          ><label
            >腾讯地图经度
            <input
              required
              type="number"
              step="any"
              min="-180"
              max="180"
              v-model.number="editing.longitude" /></label
          ><label
            >半径（米）
            <input
              required
              type="number"
              min="1"
              max="10000"
              v-model.number="editing.radius" /></label
          ><button :disabled="busy">保存</button
          ><button type="button" @click="editing = null">取消</button>
        </form>
      </dialog> </template
    ><template v-if="tab === '历史记录'"
      ><label>历史月份 <input type="month" v-model="historyMonth" /></label>
      <button @click="readHistory">查询</button>
      <p v-for="(r, i) in historical" :key="i">
        {{ r.day }} · {{ r.name }} · {{ r.kind }} · {{ r.detail }}
      </p>
      <p v-if="historical && !historical.length">
        该月没有旧版考勤、排班或流水。
      </p></template
    >
  </section>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { get, post, put, buildProtectedFileUrl } from '@/utils/request'
const tabs = [
    '每日考勤',
    '工时审批',
    '休假审批',
    '统一规则',
    '项目地点',
    '历史记录',
  ],
  tab = ref(tabs[0])
const labels: Record<string, string> = {
  full: '满勤',
  normal: '满勤（经理认定）',
  shortage: '已确认缺工时',
  pending_hours: '工时异常 · 待审批',
  in_progress: '班次结算前',
  present: '已打卡',
  conflict: '项目异常',
  waiting: '今日未打卡',
  quota_rest: '额度休假',
  rest: '休息',
  absent: '缺勤',
  approved_rest: '已批额外休息',
  holiday: '公司节假日',
  trip: '全天出差',
  leave: '已批请假',
}
const shiftLabel = (kind?: string) =>
  kind === 'night' ? '夜班（晚班）' : kind === 'day' ? '白班（早班）' : '—'
const duration = (minutes: number) =>
  Math.floor(minutes / 60) +
  '小时' +
  (minutes % 60 ? (minutes % 60) + '分' : '')
const states: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
}
const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
const date = ref(today),
  search = ref(''),
  status = ref(''),
  rows = ref<any[]>([]),
  ctx = ref<any>({ policies: [] }),
  requests = ref<any[]>([]),
  places = ref<any[]>([]),
  selected = ref(0),
  busy = ref(false),
  error = ref(''),
  notes = ref<Record<number, string>>({}),
  placeQuery = ref(''),
  editing = ref<any>(null)
const form = ref({ effective: today, photo: false })
const pendingHours = computed(() =>
  rows.value.flatMap((r) =>
    r.days
      .filter((d: any) => d.hours_status === 'pending_hours')
      .map((d: any) => ({ r, d })),
  ),
)
function changeMonth(e: Event) {
  const month = (e.target as HTMLInputElement).value
  if (month) {
    date.value = month + '-01'
    load()
  }
}
const reviewDialog = ref<HTMLDialogElement>(),
  reviewing = ref<any>(),
  outcome = ref(''),
  missing = ref<number | null>(null),
  reviewReason = ref('')
function openReview(r: any, d: any) {
  reviewing.value = { name: r.name, d }
  outcome.value = ''
  missing.value = d.suggested_minutes
  reviewReason.value = ''
  error.value = ''
  reviewDialog.value?.showModal()
}
const submitHours = () =>
  write(async () => {
    await post('/monthly/hours/review', {
      punch_id: reviewing.value.d.revision,
      outcome: outcome.value,
      missing_minutes: outcome.value === 'shortage' ? missing.value : 0,
      reason: reviewReason.value,
    })
    reviewDialog.value?.close()
  })
const filteredPlaces = computed(() =>
  places.value.filter((p) => (p.code + p.name).includes(placeQuery.value)),
)
const detail = computed(() =>
  rows.value.find((r) => r.employee_id === selected.value),
)
const visibleRows = computed(() =>
  rows.value
    .map((r) => ({ ...r, day: r.days.find((d: any) => d.day === date.value) }))
    .filter(
      (r) =>
        r.day &&
        (r.name + r.employee_no).includes(search.value) &&
        (!status.value || r.day.status === status.value),
    ),
)
const fmt = (s: string) => s.replace('T', ' ').slice(0, 16)
const requestText = (d: any) =>
  d.application
    ? '休假' + states[d.application.status] + ' ' + d.application.note
    : ''
const file = ref<File>(),
  preview = ref<any>()
function pickFile(e: Event) {
  file.value = (e.target as HTMLInputElement).files?.[0]
  preview.value = null
}
const importPlaces = (commit: boolean) =>
  write(async () => {
    if (!file.value) return
    const data = new FormData()
    data.append('file', file.value)
    if (commit) {
      data.append('preview_token', preview.value.preview_token)
      await post('/field/projects/import/commit', data)
      preview.value = null
    } else preview.value = await post('/field/projects/import/preview', data)
  })
const historyMonth = ref(today.slice(0, 7)),
  historical = ref<any[]>()
async function readHistory() {
  try {
    historical.value = await get('/monthly/history', {
      month: historyMonth.value + '-01',
    })
  } catch (e: any) {
    error.value = e.response?.data?.detail || '历史记录读取失败'
  }
}
let revision = 0
async function load() {
  const version = ++revision
  try {
    const [c, r, a, p] = await Promise.all([
      get('/monthly/context'),
      get('/monthly/report', { month: date.value.slice(0, 7) + '-01' }),
      get('/monthly/requests'),
      get('/monthly/places'),
    ])
    if (version === revision) {
      ctx.value = c
      rows.value = r
      requests.value = a
      places.value = p
      error.value = ''
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || '读取失败，请刷新'
  }
}
async function write(action: () => Promise<any>) {
  if (busy.value) return
  busy.value = true
  try {
    await action()
    await load()
  } catch (e: any) {
    error.value = String(e.response?.data?.detail || '保存失败')
  } finally {
    busy.value = false
  }
}
const review = (r: any, approved: boolean) =>
  write(() =>
    post('/monthly/requests/' + r.id + '/review', {
      approved,
      note: notes.value[r.id] || '',
    }),
  )
const confirmDay = (d: any, code: string) =>
  write(() => post('/monthly/confirm', { punch_id: d.revision, code }))
const saveRule = () =>
  write(() =>
    post('/monthly/policy', {
      ...form.value,
      minutes: 720,
      cutoff: 0,
    }),
  )
const savePlace = () =>
  write(async () => {
    await put(
      '/monthly/places/' + encodeURIComponent(editing.value.code),
      editing.value,
    )
    editing.value = null
  })
onMounted(async () => {
  try {
    ctx.value = await get('/monthly/context')
    const requested = new URLSearchParams(location.search).get('date')
    date.value =
      requested && /^\d{4}-\d{2}-\d{2}$/.test(requested)
        ? requested
        : ctx.value.day
  } catch {}
  await load()
})
</script>
<style scoped>
.monthly {
  background: #fff;
  padding: 24px;
  border-radius: 12px;
  color: #203047;
}
header,
nav,
.filters {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
header {
  justify-content: space-between;
}
h1,
h2 {
  margin: 8px 0;
}
nav,
.filters {
  margin: 16px 0;
}
button,
input,
select {
  padding: 7px 10px;
  border: 1px solid #c8d4df;
  border-radius: 5px;
  background: white;
}
button {
  cursor: pointer;
  color: #154e70;
}
button[aria-pressed='true'] {
  background: #154e70;
  color: white;
}
table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}
th,
td,
.entry {
  padding: 12px 6px;
  border-bottom: 1px solid #ddd;
}
small {
  display: block;
  color: #526273;
}
.pill {
  display: inline-block;
  background: #e4eaf0;
  padding: 4px 9px;
  border-radius: 12px;
}
.projectGreen {
  color: #166637;
  font-weight: 600;
}
.full,
.normal,
.present {
  background: #d6f2df;
  color: #16542f;
}
.quota_rest,
.rest,
.waiting {
  background: #fff0b6;
  color: #6a4c00;
}
.absent {
  background: #ffd7d5;
  color: #95171e;
}
.pending_hours,
.shortage,
.conflict {
  background: #ffe2c1;
  color: #803d0b;
}
article,
form {
  padding: 15px;
  margin-top: 12px;
  background: #f6f8fa;
}
form {
  display: grid;
  gap: 12px;
  max-width: 520px;
}
dialog {
  position: fixed;
  top: 15%;
  z-index: 10;
  border: 1px solid #aebccc;
}
p[role='alert'] {
  color: #a22727;
}
</style>
