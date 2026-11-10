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
            <dialog ref="shiftDialog" aria-label="选择上班班次">
              <h2>选择本次上班班次</h2>
              <button @click="chooseShift('day')">白班（早班）</button>
              <button @click="chooseShift('night')">夜班（晚班）</button>
              <button v-if="ctx.earned_rest" @click="previewRest(ctx.day)">
                休假
              </button>
              <button @click="shiftDialog?.close()">取消</button>
            </dialog>
            <dialog ref="restDialog" aria-label="确认整天休假">
              <form v-if="quote" @submit.prevent="confirmRest">
                <h2>{{ quote.day }} · 休假 1 天</h2>
                <p>
                  当前余额 {{ quote.balance }} 天 ·
                  {{ quote.mode === 'direct' ? '休后' : '获批后' }}余额
                  {{ quote.after }} 天
                </p>
                <label v-if="quote.mode === 'request'"
                  >申请原因
                  <input v-model.trim="reason" required maxlength="500"
                /></label>
                <button :disabled="busy">
                  {{
                    quote.mode === 'direct' ? '确认休假' : '提交售后经理审批'
                  }}
                </button>
                <button type="button" @click="restDialog?.close()">取消</button>
              </form>
            </dialog>
          </section>
          <label
            >考勤月份 <input type="month" v-model="month" @change="refresh"
          /></label>
          <p>
            实际打卡 {{ rows[0]?.punch_count || 0 }} 次 · 审批补卡 {{ rows[0]?.corrected_count || 0 }} 次 · 满勤
            {{ rows[0]?.full_days || 0 }} 天 · 已休
            {{ rows[0]?.leave_days || 0 }} 天 · 缺卡
            {{ rows[0]?.missing_punches || 0 }} 次 · 缺勤
            {{ rows[0]?.absence_days || 0 }} 天
          </p>
          <p v-if="rows[0]?.earned_rest">
            休假余额 {{ rows[0].rest_balance }} 天 · 攒假进度
            {{ rows[0].work_progress }}/6 天
          </p>
          <p v-if="rows[0]">
            已确认缺工时 {{ duration(rows[0].shortage_minutes) }}（{{
              rows[0].shortage_days
            }}
            天）
            <small v-if="rows[0].pending_hours_days"
              >待审 {{ rows[0].pending_hours_days }} 天 · 可核算
              {{ duration(rows[0].pending_estimate_minutes)
              }}<template v-if="rows[0].pending_unknown_days">
                · {{ rows[0].pending_unknown_days }} 天待核定</template
              ></small
            >
          </p>
          <details
            v-for="d in [...(rows[0]?.days || [])].reverse()"
            :key="d.day"
          >
            <summary>
              {{ d.day }}　<span :class="d.status">{{ labels[d.status] }}</span
              >　{{ d.project?.name }}
            </summary>
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
          <h2>休假登记与补休</h2>
          <form v-if="ctx.earned_rest" @submit.prevent="previewRest(restDay)">
            <input
              aria-label="休假日期"
              type="date"
              required
              v-model="restDay"
              :min="ctx.earned_from"
              :max="ctx.day"
            />
            <button :disabled="busy">查看余额并确认</button>
          </form>
          <form v-else @submit.prevent="apply">
            <input
              aria-label="休息日期"
              type="date"
              required
              v-model="restDay"
            /><input
              aria-label="原因"
              required
              maxlength="500"
              placeholder="申请原因"
              v-model.trim="reason"
            /><button :disabled="busy">提交整天休息申请</button>
          </form>
          <p v-for="r in applications" :key="r.day">
            {{ r.day }}　{{ states[r.status] }}　{{ r.reason }} {{ r.note }}
          </p>
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
    restDay.value ||= day
    const route = manager.value ? '/monthly/report' : '/monthly/me'
    rows.value = await get(route, { month: month.value + '-01' })
    if (!manager.value) {
      const current =
        month.value === day.slice(0, 7)
          ? rows.value
          : await get(route, { month: day.slice(0, 7) + '-01' })
      currentDay.value = current[0]?.days.find((d: any) => d.day === day)
    }
    applications.value = await get(
      manager.value ? '/monthly/requests' : '/monthly/me/requests',
    )
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
  if (ctx.value.shifts && nextKind.value === 'in' && !ctx.value.shift)
    shiftDialog.value?.showModal()
  else punch()
}
const restDialog = ref<HTMLDialogElement>(),
  quote = ref<any>()
async function previewRest(day: string) {
  if (busy.value) return
  shiftDialog.value?.close()
  busy.value = true
  error.value = ''
  try {
    quote.value = await get('/monthly/me/rest-preview', { day })
    reason.value = ''
    restDialog.value?.showModal()
  } catch (e: any) {
    error.value = e.response?.data?.detail || '读取休假余额失败'
  } finally {
    busy.value = false
  }
}
const confirmRest = () =>
  action(async () => {
    restDialog.value?.close()
    const result = await post('/monthly/rest', {
      day: quote.value.day,
      mode: quote.value.mode,
      expected_balance: quote.value.balance,
      reason: reason.value,
    })
    feedback.value =
      result.status === 'approved' ? '已确认休假' : '已提交售后经理审批'
  })
function chooseShift(shift: string) {
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
const apply = () =>
  action(async () => {
    await post('/monthly/rest', { day: restDay.value, reason: reason.value })
    reason.value = ''
  })
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
