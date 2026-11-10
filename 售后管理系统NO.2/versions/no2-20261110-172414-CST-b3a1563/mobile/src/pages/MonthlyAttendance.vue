<template>
  <ion-page
    ><ion-content
      ><main>
        <header>
          <div>{{ auth.user?.name }}</div>
          <button @click="logout">退出</button>
        </header>
        <h1>{{ manager ? '员工月度考勤' : '我的月度考勤' }}</h1>
        <p role="alert" v-if="error">{{ error }}</p>
        <p v-if="!ctx.active">等待售后经理设置生效日期。</p>
        <template v-if="manager"
          ><label
            >月份 <input type="month" v-model="month" @change="refresh"
          /></label>
          <article v-for="r in rows" :key="r.employee_id">
            <b>{{ r.name }}</b> · {{ labels[r.days.at(-1)?.status] }}
            {{ shiftLabel(r.days.at(-1)?.shift) }} ·
            {{ r.days.at(-1)?.project?.name
            }}<small
              >实际打卡 {{ r.punch_count }} 次 · 审批补卡 {{ r.corrected_count }} 次 · 满勤 {{ r.full_days }} 天 · 已休
              {{ r.leave_days }} 天 · 缺卡 {{ r.missing_punches }} 次 · 缺勤
              {{ r.absence_days }} 天 · 已确认缺
              {{ duration(r.shortage_minutes) }}（{{
                r.shortage_days
              }}
              天）<template v-if="r.earned_rest">
                · 余额 {{ r.rest_balance }} 天 · 进度
                {{ r.work_progress }}/6</template
              ></small
            >
          </article>
          <router-link to="/app/manager-inbox">审批中心 · 待我处理 {{managerCount ?? '…'}}</router-link>
          <h2>休假审批</h2>
          <article v-for="r in applications" :key="r.id">
            {{ r.name }} · {{ r.day }} · {{ r.reason }}
            <small>{{ states[r.status] }}</small
            ><template v-if="r.status === 'pending'"
              ><button @click="decide(r.id, true)">批准</button
              ><button @click="decide(r.id, false)">驳回</button></template
            >
          </article></template
        >
        <template v-else>
          <section>
            <strong
              >{{ ctx.day }} ·
              {{ labels[currentDay?.status] || '待打卡' }}</strong
            >
            <p>每日应出勤 {{ (ctx.minutes || 0) / 60 }} 小时</p>
            <p v-if="ctx.shift">
              {{ shiftLabel(ctx.shift) }} · 已记录
              {{ duration(Math.floor((currentDay?.worked_seconds || 0) / 60)) }}
            </p>
            <p v-if="feedback" role="status">{{ feedback }}</p>
            <p v-if="currentDay?.project">{{ currentDay.project.name }}</p>
            <label v-if="ctx.photo"
              >打卡照片
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                capture="environment"
                @change="choose"
            /></label>
            <button
              class="primary"
              :disabled="busy || !ctx.active"
              @click="startPunch"
            >
              {{
                busy
                  ? '正在提交…'
                  : nextKind === 'in'
                    ? '定位并上班打卡'
                    : '定位并下班打卡'
              }}
            </button>
            <button
              v-if="ctx.resume_night_day"
              :disabled="busy"
              @click="punch('night', ctx.resume_night_day)"
            >
              继续 {{ ctx.resume_night_day }} 的夜班
            </button>
            <dialog ref="shiftDialog" aria-label="选择打卡方式">
              <h2>选择打卡方式</h2>
              <button v-if="!ctx.shifts" @click="chooseShift()">上班打卡</button>
              <button v-if="ctx.shifts" @click="chooseShift('day')">白班（早班）</button>
              <button v-if="ctx.shifts" @click="chooseShift('night')">夜班（晚班）</button>
              <button @click="openRest">
                休假
              </button>
              <button @click="shiftDialog?.close()">取消</button>
            </dialog>
            <dialog ref="restDialog" aria-label="确认整天休假">
              <form @submit.prevent="confirmRest">
                <h2>休假 1 天</h2>
                <label>休假日期
                  <input type="date" required v-model="restDay" :max="ctx.day"
                    :min="ctx.earned_rest ? ctx.earned_from : undefined" @change="loadQuote" />
                </label>
                <p v-if="quote">当前余额 {{ quote.balance }} 天 ·
                  {{ quote.mode === 'direct' ? '休后' : '获批后' }}余额 {{ quote.after }} 天</p>
                <p v-if="restError" role="alert">{{ restError }}</p>
                <label v-if="!ctx.earned_rest || quote?.mode === 'request'">申请原因
                  <input v-model.trim="reason" required maxlength="500" /></label>
                <button :disabled="busy || !restDay || (ctx.earned_rest && quote?.day !== restDay)">
                  {{ quote?.mode === 'direct' ? '确认休假' : '提交售后经理审批' }}
                </button>
                <button type="button" @click="restDialog?.close()">取消</button>
              </form>
            </dialog>
          </section>
          <label
            >考勤月份 <input type="month" v-model="month" @change="refresh"
          /></label>
          <template v-if="report">
            <dl class="month-totals">
              <div><dt>本月满勤</dt><dd>{{ report.full_days }} <span>天</span></dd></div>
              <div><dt>休假余额</dt><dd>{{ report.rest_balance < 0 ? '−' + Math.abs(report.rest_balance) : report.rest_balance }} <span>天</span></dd></div>
            </dl>
            <button v-if="alertText" class="month-alert" type="button" aria-controls="employee-history" @click="showExceptions">
              {{ alertText }} <span aria-hidden="true">›</span>
            </button>
            <details :key="month" class="month-detail">
              <summary>查看月度明细</summary>
              <dl class="month-stats">
                <div><dt>实际打卡</dt><dd>{{ report.punch_count }} 次</dd></div>
                <div><dt>审批补卡</dt><dd>{{ report.corrected_count }} 次</dd></div>
                <div><dt>已休</dt><dd>{{ report.leave_days }} 天</dd></div>
                <div><dt>缺卡</dt><dd>{{ report.missing_punches }} 次</dd></div>
                <div><dt>缺勤</dt><dd>{{ report.absence_days }} 天</dd></div>
                <div v-if="report.earned_rest"><dt>攒假进度</dt><dd>{{ report.work_progress }}/6 天</dd></div>
                <div><dt>已确认缺工时</dt><dd>{{ duration(report.shortage_minutes) }}（{{ report.shortage_days }} 天）</dd></div>
                <div v-if="report.pending_hours_days"><dt>待审批工时异常</dt><dd>{{ report.pending_hours_days }} 天</dd></div>
                <div v-if="report.pending_hours_days > report.pending_unknown_days"><dt>可核算差额</dt><dd>{{ duration(report.pending_estimate_minutes) }}</dd></div>
                <div v-if="report.pending_unknown_days"><dt>缺卡时长</dt><dd>{{ report.pending_unknown_days }} 天待核定</dd></div>
              </dl>
            </details>
          </template>
          <p v-else-if="loadedMonth === month">该月暂无考勤记录</p>
          <details class="history" id="employee-history" ref="historyPanel" :key="'history-' + month">
            <summary>历史打卡记录</summary>
            <details
              v-for="d in [...(report?.days || [])].reverse()"
              :key="d.day"
              :data-day="d.day"
            >
              <summary>
                {{ d.day }}　<span :class="d.status">{{ labels[d.status] }}</span
                >　{{ d.project?.name }}
              </summary>
              <small v-if="d.shift"
                >{{ shiftLabel(d.shift) }} · 实际工时
                {{ d.missing_punches && d.suggested_minutes == null ? '待核定' : duration(Math.floor(d.worked_seconds / 60)) }} ·
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
                {{ duration(d.review.missing_minutes) }}</small
              >
              <p>{{ d.holiday }} {{ d.anomalies.join('、') }}</p>
              <small v-if="d.status === 'absent'"
                >{{ d.start.replace('T', ' ') }} 至
                {{ d.end.replace('T', ' ') }}</small
              ><small v-for="p in d.punches" :key="p.id"
                >{{ p.corrected ? '审批补卡 · ' : '' }}{{ p.kind === 'in' ? '上班' : '下班' }}
                {{ p.at.slice(5, 19).replace('T', ' ') }} ·
                {{ p.matches.map((m: any) => m.name).join(' / ') }}</small
              >
              <small v-for="h in d.prior_hours_reviews" :key="h.at">补卡前的工时审批 · {{ labels[h.outcome] }} · {{ h.reason }}</small>
              <small v-if="d.application">休假{{ states[d.application.status] }} · {{ d.application.reason }} {{ d.application.note }}</small>
              <MonthlyCorrection :day="d" @submitted="refresh" />
            </details>
            <details>
              <summary>查询所选月份的旧版记录</summary>
              <button @click="history">查询历史打卡、排班与轮休流水</button>
              <p v-for="(r, i) in oldRows" :key="i">
                {{ r.day }} · {{ r.kind }} · {{ r.detail }}
              </p>
              <small v-if="oldRows && !oldRows.length">该月无旧版记录</small>
            </details>
          </details>
          <router-link to="/app/approval/start"
            >请假、出差及其他申请</router-link
          ></template
        >
        <p><router-link to="/app/tabs/approvals">审批记录</router-link></p>
      </main></ion-content
    ></ion-page
  >
</template>
<script setup lang="ts">
import MonthlyCorrection from './MonthlyCorrection.vue'
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import gcoord from 'gcoord'
import { IonPage, IonContent, onIonViewWillEnter } from '@ionic/vue'
import { useRouter } from 'vue-router'
import { get, post } from '@/utils/request'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore(),
  router = useRouter()
const shiftLabel = (kind?: string) =>
  kind === 'night' ? '夜班（晚班）' : kind === 'day' ? '白班（早班）' : '—'
const duration = (minutes: number) =>
  Math.floor(minutes / 60) +
  '小时' +
  (minutes % 60 ? (minutes % 60) + '分' : '')
const labels: Record<string, string> = {
  full: '满勤',
  normal: '满勤（经理认定）',
  shortage: '已确认缺工时',
  pending_hours: '工时异常 · 待审批',
  in_progress: '班次结算前',
  present: '已打卡',
  conflict: '项目异常 · 待确认',
  waiting: '今日未打卡',
  quota_rest: '额度休假',
  rest: '休息',
  absent: '缺勤',
  approved_rest: '已批额外休息',
  holiday: '公司节假日',
  trip: '全天出差',
  leave: '已批请假',
}
const states: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
}
const ctx = ref<any>({}),
  rows = ref<any[]>([]),
  applications = ref<any[]>([]),
  currentDay = ref<any>(),
  month = ref(''),
  restDay = ref(''),
  reason = ref(''),
  photo = ref<File>(),
  busy = ref(false),
  error = ref('')
const manager = computed(() => auth.roles.includes('aftersales_manager')),
  nextKind = computed(() =>
    ctx.value.shifts
      ? ctx.value.next_kind
      : currentDay.value?.punches.at(-1)?.kind === 'in'
        ? 'out'
        : 'in',
  )
import {useManagerInboxCount} from '../../../shared/useManagerInboxCount'
const managerCount=useManagerInboxCount(()=>manager.value,get)
const loadedMonth = ref('')
const report = computed(() => loadedMonth.value === month.value ? rows.value[0] : undefined)
const historyPanel = ref<HTMLDetailsElement>()
function projectUnconfirmed(d: any) {
  return !d.project && d.projects.length > 1
}
const exceptionDays = computed(() => new Set<string>((report.value?.days || [])
  .filter((d: any) => d.missing_punches || d.status === 'absent' ||
    ['pending_hours', 'shortage'].includes(d.hours_status) || projectUnconfirmed(d))
  .map((d: any) => d.day)))
const alertText = computed(() => {
  const r = report.value
  if (!r) return ''
  const projects = r.days.filter(projectUnconfirmed).length
  return [
    r.missing_punches && `缺卡 ${r.missing_punches} 次`,
    r.pending_hours_days && `工时待核定 ${r.pending_hours_days} 天`,
    r.absence_days && `缺勤 ${r.absence_days} 天`,
    r.shortage_days && `已确认缺工时 ${r.shortage_days} 天`,
    projects && `项目待确认 ${projects} 天`,
  ].filter(Boolean).join(' · ')
})
function showExceptions() {
  const history = historyPanel.value
  if (!history) return
  history.open = true
  let first: HTMLDetailsElement | undefined
  for (const row of history.querySelectorAll<HTMLDetailsElement>('details[data-day]')) {
    row.open = exceptionDays.value.has(row.dataset.day || '')
    if (row.open) first ??= row
  }
  first?.querySelector('summary')?.focus({ preventScroll: true })
  first?.scrollIntoView({block: 'start'})
}
const oldRows = ref<any[]>()
const history = () =>
  action(async () => {
    oldRows.value = await get('/monthly/history', {
      month: month.value + '-01',
    })
  })
const choose = (e: Event) => {
  photo.value = (e.target as HTMLInputElement).files?.[0]
}
async function refresh() {
  try {
    ctx.value = await get('/monthly/context')
    const day = ctx.value.day
    month.value ||= day.slice(0, 7)
    const route = manager.value ? '/monthly/report' : '/monthly/me'
    const selected = month.value
    const result = await get(route, { month: selected + '-01' })
    if (selected !== month.value) return
    if (loadedMonth.value !== selected) oldRows.value = undefined
    loadedMonth.value = selected
    rows.value = result
    if (!manager.value) {
      const current =
        month.value === day.slice(0, 7)
          ? rows.value
          : await get(route, { month: day.slice(0, 7) + '-01' })
      currentDay.value = current[0]?.days.find((d: any) => d.day === day)
    }
    if (manager.value) applications.value = await get('/monthly/requests')
  } catch (e: any) {
    error.value = String(e.response?.data?.detail || '考勤读取失败')
  }
}
async function action(fn: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await fn()
    await refresh()
  } catch (e: any) {
    error.value = String(
      e.response?.data?.detail || e.message || '操作失败，请重试',
    )
  } finally {
    busy.value = false
  }
}
const shiftDialog = ref<HTMLDialogElement>(),
  feedback = ref('')
async function startPunch() {
  if (busy.value) return
  busy.value = true
  try {
    ctx.value = await get('/monthly/context')
  } catch {
    error.value = '读取当前班次失败，请重试'
    return
  } finally {
    busy.value = false
  }
  if (nextKind.value === 'in' && !ctx.value.shift)
    shiftDialog.value?.showModal()
  else punch()
}
const restDialog = ref<HTMLDialogElement>(),
  quote = ref<any>(),
  restError = ref('')
function openRest() {
  shiftDialog.value?.close()
  reason.value = ''
  restDay.value = ctx.value.day
  restDialog.value?.showModal()
  void loadQuote()
}
let previewRevision = 0
async function loadQuote() {
  const revision = ++previewRevision
  quote.value = undefined
  restError.value = ''
  if (!ctx.value.earned_rest || !restDay.value) return
  try {
    const result = await get('/monthly/me/rest-preview', { day: restDay.value })
    if (revision === previewRevision) quote.value = result
  } catch (e: any) {
    if (revision === previewRevision) restError.value = e.response?.data?.detail || '读取休假余额失败'
  }
}
const confirmRest = () =>
  action(async () => {
    if (ctx.value.earned_rest && quote.value?.day !== restDay.value) return
    const result = await post('/monthly/rest', {
      day: restDay.value,
      mode: quote.value?.mode,
      expected_balance: quote.value?.balance,
      reason: reason.value,
    }).catch((e: any) => {
      restError.value = e.response?.data?.detail || '休假提交失败，请重试'
      throw e
    })
    restDialog.value?.close()
    feedback.value = result.status === 'approved' ? '已确认休假' : '已提交售后经理审批'
  })
function chooseShift(shift?: string) {
  shiftDialog.value?.close()
  punch(shift)
}
async function punch(shift?: string, day = ctx.value.day) {
  await action(async () => {
    if (ctx.value.photo && !photo.value) throw Error('请先拍摄打卡照片')
    if (!navigator.geolocation) throw Error('浏览器未提供定位，请检查位置权限')
    const pos = await new Promise<GeolocationPosition>((ok, fail) =>
      navigator.geolocation.getCurrentPosition(
        ok,
        () => fail(Error('定位失败，请检查位置权限后重试')),
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
      ),
    )
    const [longitude, latitude] = gcoord.transform(
      [pos.coords.longitude, pos.coords.latitude],
      gcoord.WGS84,
      gcoord.GCJ02,
    )
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude))
      throw Error('定位坐标转换失败，请重新定位')
    let url = ''
    if (ctx.value.photo && photo.value) {
      const data = new FormData()
      data.append('file', photo.value)
      url = (await post('/upload?category=attendance', data)).url
    }
    const kind = nextKind.value
    const result = await post('/monthly/punch', {
      latitude,
      longitude,
      photo: url,
      request_key: crypto.randomUUID(),
      kind,
      shift,
      attendance_day: day,
    })
    feedback.value = '打卡成功'
    if (ctx.value.shifts && kind === 'out') {
      const report = await get('/monthly/me', {
        month: result.day.slice(0, 7) + '-01',
      })
      const row = report[0]?.days.find((d: any) => d.day === result.day)
      if (row)
        feedback.value +=
          ' · ' +
          result.day +
          ' ' +
          shiftLabel(row.shift) +
          (row.remaining_minutes
            ? '，距 12 小时还差 ' + duration(row.remaining_minutes)
            : '，已满 12 小时')
    }
    photo.value = undefined
  })
}
const decide = (id: number, approved: boolean) =>
  action(() => post('/monthly/requests/' + id + '/review', { approved }))
function logout() {
  auth.logout()
  router.replace('/login')
}
onIonViewWillEnter(refresh)
onMounted(() => window.addEventListener("focus", refresh))
onBeforeUnmount(() => window.removeEventListener("focus", refresh))
</script>
<style scoped>
main {
  padding: 20px;
  max-width: 620px;
  margin: auto;
  color: #203144;
}
h1 {
  font-size: 24px;
}
h2 {
  font-size: 18px;
}
header,
form {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
header {
  justify-content: space-between;
}
section,
article,
details {
  background: #f3f7fa;
  padding: 14px;
  margin: 10px 0;
  border-radius: 8px;
}
.month-totals, .month-stats { margin: 12px 0; }
.month-totals { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); padding: 16px 0; border-block: 1px solid #dce5e9; }
.month-totals > div + div { border-left: 1px solid #dce5e9; padding-left: 20px; }
.month-totals dt { color: #526571; font-size: 14px; }
.month-totals dd { margin: 4px 0 0; font-size: 26px; font-weight: 650; }
.month-totals dd span { font-size: 14px; font-weight: 400; }
.month-alert { width: 100%; text-align: left; color: #724000; background: #fff5de; border: 0; padding: 10px 12px; }
.month-alert span { float: right; }
.month-stats div { display: flex; justify-content: space-between; gap: 10px; padding-block: 4px; }
.month-stats dd { margin: 0; text-align: right; }
.month-detail > summary, .history > summary {
  cursor: pointer;
  font-weight: 600;
}
.history > details {
  background: white;
}
small {
  display: block;
  margin-top: 5px;
}
button,
input {
  padding: 8px;
  border-radius: 6px;
  border: 1px solid #adbfc9;
  background: white;
}
.primary {
  display: block;
  background: #075868;
  color: white;
  margin-top: 12px;
  width: 100%;
}
.full,
.normal,
.present {
  color: #166637;
}
.waiting,
.rest,
.quota_rest {
  color: #836000;
}
.absent,
p[role='alert'] {
  color: #a02127;
}
.pending_hours,
.shortage,
.conflict {
  color: #933f06;
}
</style>
