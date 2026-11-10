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
            {{ r.days.at(-1)?.project?.name
            }}<small
              >本月已休 {{ r.rest_used }} 天 · 缺勤
              {{ r.absence_days }} 天</small
            >
          </article>
          <h2>额外休息审批</h2>
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
              @click="punch"
            >
              {{
                busy
                  ? '正在提交…'
                  : nextKind === 'in'
                    ? '定位并上班打卡'
                    : '定位并下班打卡'
              }}
            </button>
          </section>
          <label
            >考勤月份 <input type="month" v-model="month" @change="refresh"
          /></label>
          <p>
            已用休息 {{ rows[0]?.rest_used || 0 }} / 4 天　缺勤
            {{ rows[0]?.absence_days || 0 }} 天
          </p>
          <details
            v-for="d in [...(rows[0]?.days || [])].reverse()"
            :key="d.day"
          >
            <summary>
              {{ d.day }}　<span :class="d.status">{{ labels[d.status] }}</span
              >　{{ d.project?.name }}
            </summary>
            <p>{{ d.holiday }} {{ d.anomalies.join('、') }}</p>
            <small v-if="d.status === 'absent'"
              >{{ d.start.replace('T', ' ') }} 至
              {{ d.end.replace('T', ' ') }}</small
            ><small v-for="p in d.punches" :key="p.id"
              >{{ p.kind === 'in' ? '上班' : '下班' }}
              {{ p.at.slice(11, 19) }} ·
              {{ p.matches.map((m: any) => m.name).join(' / ') }}</small
            >
          </details>
          <details>
            <summary>查询所选月份的旧版记录</summary>
            <button @click="history">查询历史打卡、排班与轮休流水</button>
            <p v-for="(r, i) in oldRows" :key="i">
              {{ r.day }} · {{ r.kind }} · {{ r.detail }}
            </p>
            <small v-if="oldRows && !oldRows.length">该月无旧版记录</small>
          </details>
          <h2>额外休息申请</h2>
          <form @submit.prevent="apply">
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
import { ref, computed } from 'vue'
import gcoord from 'gcoord'
import { IonPage, IonContent, onIonViewWillEnter } from '@ionic/vue'
import { useRouter } from 'vue-router'
import { get, post } from '@/utils/request'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore(),
  router = useRouter()
const labels: Record<string, string> = {
  present: '已打卡',
  conflict: '项目异常 · 待确认',
  waiting: '今日未打卡',
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
    currentDay.value?.punches.at(-1)?.kind === 'in' ? 'out' : 'in',
  )
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
async function punch() {
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
    await post('/monthly/punch', {
      latitude,
      longitude,
      photo: url,
      request_key: crypto.randomUUID(),
      kind: nextKind.value,
    })
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
.present {
  color: #166637;
}
.waiting,
.rest {
  color: #836000;
}
.absent,
p[role='alert'] {
  color: #a02127;
}
.conflict {
  color: #933f06;
}
</style>
