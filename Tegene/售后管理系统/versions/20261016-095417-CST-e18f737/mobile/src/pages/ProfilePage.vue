<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page profile-enterprise-page">
      <div class="mobile-shell profile-shell">
        <section class="profile-identity-card" aria-label="我的员工信息">
          <div class="profile-identity-card__top">
            <span class="profile-avatar" aria-hidden="true">{{ avatarText }}</span>
            <div>
              <span class="profile-kicker">我的</span>
              <h1>{{ auth.user?.name || '-' }}</h1>
              <p>{{ auth.user?.department || '-' }} / {{ auth.user?.position || roleLabel }}</p>
            </div>
            <button class="profile-settings-button" type="button" aria-label="安全设置" @click="openSecuritySheet">
              <ion-icon :icon="settingsOutline" />
            </button>
          </div>

          <div class="profile-info-line">
            <span>ID {{ auth.user?.id || '-' }}</span>
            <span>{{ roleLabel }}</span>
            <span>在职</span>
          </div>

          <label class="profile-search" aria-label="搜索个人服务">
            <ion-icon :icon="searchOutline" />
            <input v-model.trim="profileQuery" type="search" placeholder="搜索个人档案、单据、服务" />
          </label>

          <div class="profile-status-strip">
            <div>
              <span>资料完整度</span>
              <strong>{{ profileCompletion }}%</strong>
            </div>
            <div>
              <span>待办审批</span>
              <strong>{{ inbox.approvalDisplayCount }}</strong>
            </div>
            <div>
              <span>通知未读</span>
              <strong>{{ inbox.state.unreadNotifications }}</strong>
            </div>
          </div>
        </section>

        <section class="profile-panel section-block" aria-label="本周提醒">
          <div class="profile-panel__head">
            <div>
              <span class="profile-kicker">本周提醒</span>
              <h2>需要关注的事项</h2>
            </div>
            <button type="button" @click="router.push('/app/notifications')">全部</button>
          </div>
          <div class="profile-reminder-list">
            <button
              v-for="item in profileReminders"
              :key="item.title"
              type="button"
              @click="router.push(item.path)"
            >
              <span>
                <strong>{{ item.title }}</strong>
                <small>{{ item.caption }}</small>
              </span>
              <em>{{ item.badge }}</em>
            </button>
          </div>
        </section>

        <section
          v-for="group in filteredMenuGroups"
          :key="group.title"
          class="profile-panel section-block"
          :aria-label="group.title"
        >
          <div class="profile-panel__head">
            <div>
              <span class="profile-kicker">{{ group.kicker }}</span>
              <h2>{{ group.title }}</h2>
            </div>
          </div>
          <div class="profile-menu-list">
            <button
              v-for="item in group.items"
              :key="item.title"
              type="button"
              @click="openMenuItem(item.path)"
            >
              <span class="profile-menu-icon" :style="{ '--menu-accent': item.accent }">
                <ion-icon :icon="item.icon" />
              </span>
              <span>
                <strong>{{ item.title }}</strong>
                <small>{{ item.caption }}</small>
              </span>
              <ion-icon :icon="chevronForwardOutline" />
            </button>
          </div>
        </section>

        <section v-if="!filteredMenuGroups.length" class="profile-panel section-block profile-empty">
          没有匹配到相关服务
        </section>

        <button class="profile-logout-button section-block" type="button" @click="logout">退出登录</button>
      </div>

      <div v-if="profileSheetVisible" class="profile-sheet-mask" @click.self="closeProfileSheet">
        <section class="profile-security-panel profile-detail-panel" role="dialog" aria-modal="true" aria-labelledby="profile-detail-title">
          <header>
            <div>
              <span class="profile-kicker">个人档案</span>
              <h2 id="profile-detail-title">我的基础资料</h2>
            </div>
            <button type="button" aria-label="关闭个人档案" @click="closeProfileSheet">
              <ion-icon :icon="closeOutline" />
            </button>
          </header>

          <div v-if="profileLoading" class="profile-detail-loading">档案加载中...</div>
          <div v-else class="profile-detail-body">
            <div class="profile-detail-list" aria-label="基础资料">
              <div v-for="row in profileRows" :key="row.label" class="profile-detail-row">
                <span>{{ row.label }}</span>
                <strong>{{ row.value }}</strong>
              </div>
            </div>

            <form class="profile-security-form profile-detail-form" @submit.prevent="submitProfileUpdate">
              <label>
                <span>邮箱</span>
                <input v-model.trim="profileForm.email" type="email" autocomplete="email" placeholder="请输入邮箱" />
              </label>
              <label>
                <span>现居地址</span>
                <input v-model.trim="profileForm.current_address" type="text" autocomplete="street-address" placeholder="请输入现居地址" />
              </label>
              <label>
                <span>紧急联系人</span>
                <input v-model.trim="profileForm.emergency_contact" type="text" placeholder="请输入联系人" />
              </label>
              <label>
                <span>紧急联系人电话</span>
                <input v-model.trim="profileForm.emergency_phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="请输入联系电话" />
              </label>
              <p v-if="profileMessage" class="profile-security-message" :class="{ 'is-error': profileMessageType === 'error' }">
                {{ profileMessage }}
              </p>
              <button class="profile-security-submit" type="submit" :disabled="profileSaving">
                {{ profileSaving ? '保存中...' : '保存档案' }}
              </button>
            </form>
          </div>
        </section>
      </div>

      <div v-if="securitySheetVisible" class="profile-sheet-mask" @click.self="closeSecuritySheet">
        <section class="profile-security-panel" role="dialog" aria-modal="true" aria-labelledby="profile-security-title">
          <header>
            <div>
              <span class="profile-kicker">安全设置</span>
              <h2 id="profile-security-title">修改登录密码</h2>
            </div>
            <button type="button" aria-label="关闭安全设置" @click="closeSecuritySheet">
              <ion-icon :icon="closeOutline" />
            </button>
          </header>
          <form class="profile-security-form" @submit.prevent="submitChangePassword">
            <label>
              <span>当前密码</span>
              <input v-model="passwordForm.old_password" type="password" autocomplete="current-password" placeholder="请输入当前密码" />
            </label>
            <label>
              <span>新密码</span>
              <input v-model="passwordForm.new_password" type="password" autocomplete="new-password" placeholder="至少 8 位" />
            </label>
            <label>
              <span>确认新密码</span>
              <input v-model="passwordForm.confirm_password" type="password" autocomplete="new-password" placeholder="再次输入新密码" />
            </label>
            <p v-if="securityMessage" class="profile-security-message" :class="{ 'is-error': securityMessageType === 'error' }">
              {{ securityMessage }}
            </p>
            <button class="profile-security-submit" type="submit" :disabled="securitySubmitting">
              {{ securitySubmitting ? '提交中...' : '保存新密码' }}
            </button>
          </form>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IonContent, IonIcon, IonPage, onIonViewWillEnter } from '@ionic/vue'
import {
  briefcaseOutline,
  chevronForwardOutline,
  closeOutline,
  documentTextOutline,
  personCircleOutline,
  searchOutline,
  settingsOutline,
  timeOutline,
  walletOutline,
} from 'ionicons/icons'
import { roleLabels } from '@/config/access'
import { useAuthStore } from '@/stores/auth'
import { useMobileInbox } from '@/composables/useMobileInbox'
import { get, post, put } from '@/utils/request'

type ProfileDetail = Record<string, unknown>

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const profileQuery = ref('')
const profileSheetVisible = ref(false)
const profileLoading = ref(false)
const profileSaving = ref(false)
const profileMessage = ref('')
const profileMessageType = ref<'success' | 'error'>('success')
const profileDetail = ref<ProfileDetail | null>(null)
const securitySheetVisible = ref(false)
const securitySubmitting = ref(false)
const securityMessage = ref('')
const securityMessageType = ref<'success' | 'error'>('success')
const profileForm = reactive({
  email: '',
  current_address: '',
  emergency_contact: '',
  emergency_phone: '',
})
const passwordForm = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})
const inbox = useMobileInbox(
  () => auth.displayRole !== 'employee',
  () => auth.roles.includes('hr') || auth.roles.includes('finance'),
)

const roleLabel = computed(() => roleLabels[auth.displayRole])
const avatarText = computed(() => {
  const name = String(auth.user?.name || '').trim()
  return name ? name.slice(-2) : 'HR'
})
const profileCompletion = computed(() => {
  const fields = [
    auth.user?.name,
    auth.user?.phone,
    auth.user?.email,
    auth.user?.department,
    auth.user?.position,
  ]
  const filled = fields.filter((item) => String(item || '').trim()).length
  return Math.round((filled / fields.length) * 100)
})
function formatProfileValue(value: unknown, fallback = '-') {
  const text = String(value ?? '').trim()
  return text || fallback
}

function readProfileField(source: ProfileDetail, keys: string[], fallback = '-') {
  for (const key of keys) {
    const value = formatProfileValue(source[key], '')
    if (value) return value
  }
  return fallback
}

const profileRows = computed(() => {
  const source = profileDetail.value || ((auth.user || {}) as unknown as ProfileDetail)
  return [
    { label: '工号', value: readProfileField(source, ['employee_no', 'employeeNo', 'id']) },
    { label: '手机号', value: readProfileField(source, ['phone']) },
    { label: '部门', value: readProfileField(source, ['department_name', 'department']) },
    { label: '岗位', value: readProfileField(source, ['position', 'job_title']) },
    { label: '公司', value: readProfileField(source, ['company_name', 'company']) },
    { label: '入职日期', value: readProfileField(source, ['hire_date']) },
  ]
})

const profileReminders = computed(() => {
  const items = [
    {
      title: '审批提醒',
      caption: '待处理和流程进展集中查看',
      badge: `${inbox.approvalDisplayCount.value}`,
      path: '/app/tabs/approvals',
    },
    {
      title: '通知待办',
      caption: '系统公告、考勤异常与业务消息',
      badge: `${inbox.state.unreadNotifications}`,
      path: '/app/notifications',
    },
  ]
  return items
})
const menuGroups = computed(() => [
  {
    title: '个人事务',
    kicker: '自助',
    items: [
      { title: '个人档案', caption: '查看资料并维护联系方式', path: 'profile', icon: personCircleOutline, accent: '#0b63f6' },
      { title: '修改密码', caption: '更新移动端登录密码', path: 'security', icon: settingsOutline, accent: '#334155' },
      { title: '考勤记录', caption: '打卡、异常与统计', path: '/app/attendance', icon: timeOutline, accent: '#0f766e' },
      { title: '我发起的', caption: '已提交审批和处理进度', path: '/app/tabs/approvals?segment=submitted', icon: documentTextOutline, accent: '#1d4ed8' },
      { title: '抄送我的', caption: '流程知会和归档记录', path: '/app/tabs/approvals?segment=cc', icon: briefcaseOutline, accent: '#b45309' },
    ],
  },
  {
    title: '企业服务',
    kicker: '服务',
    items: [
      { title: '我的项目', caption: '参与日期与打卡规则', path: '/app/field', icon: briefcaseOutline, accent: '#475569' },
    ],
  },
])
const filteredMenuGroups = computed(() => {
  const keyword = profileQuery.value.trim().toLowerCase()
  if (!keyword) return menuGroups.value
  return menuGroups.value
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => `${item.title} ${item.caption}`.toLowerCase().includes(keyword)),
    }))
    .filter((group) => group.items.length)
})

function openMenuItem(path: string) {
  if (path === 'profile') {
    openProfileSheet()
    return
  }
  if (path === 'security') {
    openSecuritySheet()
    return
  }
  void router.push(path)
}

function syncProfileForm(source: ProfileDetail) {
  profileForm.email = readProfileField(source, ['email'], '')
  profileForm.current_address = readProfileField(source, ['current_address'], '')
  profileForm.emergency_contact = readProfileField(source, ['emergency_contact'], '')
  profileForm.emergency_phone = readProfileField(source, ['emergency_phone'], '')
}

async function loadProfileDetail() {
  profileLoading.value = true
  profileMessage.value = ''
  profileMessageType.value = 'success'
  try {
    const data = await get<ProfileDetail>('/ess/profile')
    profileDetail.value = data
    syncProfileForm(data)
  } catch (error: any) {
    profileMessageType.value = 'error'
    profileMessage.value = error?.response?.data?.detail || '档案加载失败，请稍后重试'
  } finally {
    profileLoading.value = false
  }
}

function openProfileSheet() {
  profileSheetVisible.value = true
  void loadProfileDetail()
}

function closeProfileSheet() {
  if (profileSaving.value) return
  profileSheetVisible.value = false
}

async function submitProfileUpdate() {
  profileMessage.value = ''
  profileSaving.value = true
  try {
    const data = await put<ProfileDetail>('/ess/profile', {
      email: profileForm.email.trim(),
      current_address: profileForm.current_address.trim(),
      emergency_contact: profileForm.emergency_contact.trim(),
      emergency_phone: profileForm.emergency_phone.trim(),
    })
    profileDetail.value = data
    syncProfileForm(data)
    await auth.fetchUser()
    profileMessageType.value = 'success'
    profileMessage.value = '档案已更新'
  } catch (error: any) {
    profileMessageType.value = 'error'
    profileMessage.value = error?.response?.data?.detail || '档案保存失败，请稍后重试'
  } finally {
    profileSaving.value = false
  }
}

function resetPasswordForm() {
  passwordForm.old_password = ''
  passwordForm.new_password = ''
  passwordForm.confirm_password = ''
  securityMessage.value = ''
  securityMessageType.value = 'success'
}

function openSecuritySheet() {
  resetPasswordForm()
  securitySheetVisible.value = true
}

function closeSecuritySheet() {
  if (securitySubmitting.value) return
  securitySheetVisible.value = false
}

async function submitChangePassword() {
  securityMessage.value = ''
  const oldPassword = passwordForm.old_password.trim()
  const newPassword = passwordForm.new_password.trim()
  const confirmPassword = passwordForm.confirm_password.trim()
  if (!oldPassword || !newPassword || !confirmPassword) {
    securityMessageType.value = 'error'
    securityMessage.value = '请完整填写当前密码和新密码'
    return
  }
  if (newPassword.length < 8) {
    securityMessageType.value = 'error'
    securityMessage.value = '新密码至少需要 8 位'
    return
  }
  if (newPassword !== confirmPassword) {
    securityMessageType.value = 'error'
    securityMessage.value = '两次输入的新密码不一致'
    return
  }
  securitySubmitting.value = true
  try {
    await post('/auth/change-password', {
      old_password: oldPassword,
      new_password: newPassword,
    })
    securityMessageType.value = 'success'
    securityMessage.value = '密码已修改，下次登录请使用新密码'
    passwordForm.old_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
  } catch (error: any) {
    securityMessageType.value = 'error'
    securityMessage.value = error?.response?.data?.detail || '密码修改失败，请稍后重试'
  } finally {
    securitySubmitting.value = false
  }
}

function logout() {
  auth.logout()
  router.replace('/login')
}

onIonViewWillEnter(async () => {
  if (route.query.settings === 'security') openSecuritySheet()
  if (!auth.isLoggedIn || !auth.user) return
  await inbox.loadInboxCounts()
})
</script>

<style scoped>
.profile-enterprise-page {
  --background: #f7f9fc;
}

.profile-shell {
  padding: 16px 14px calc(24px + env(safe-area-inset-bottom));
}

.profile-identity-card,
.profile-panel,
.profile-logout-button {
  border: 1px solid rgba(214, 223, 235, 0.92);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 10px 26px rgba(15, 35, 63, 0.06);
}

.profile-identity-card {
  padding: 18px 16px 16px;
}

.profile-identity-card__top,
.profile-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.profile-avatar {
  flex: 0 0 auto;
  width: 50px;
  height: 50px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #0b63f6;
  color: #ffffff;
  font-size: 15px;
  font-weight: 900;
}

.profile-kicker {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.profile-identity-card h1,
.profile-panel__head h2 {
  margin: 6px 0 0;
  color: #111827;
  font-size: 22px;
  font-weight: 800;
  line-height: 1.18;
}

.profile-panel__head h2 {
  font-size: 17px;
}

.profile-identity-card p {
  margin: 8px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.45;
}

.profile-settings-button {
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #eef6ff;
  color: #0b63f6;
  font-size: 20px;
}

.profile-info-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.profile-info-line span {
  min-height: 26px;
  padding: 0 9px;
  border: 1px solid #dbe4ef;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  color: #475569;
  background: #f8fafc;
  font-size: 12px;
  font-weight: 800;
}

.profile-search {
  min-height: 44px;
  margin-top: 16px;
  padding: 0 12px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 9px;
  background: #f8fafc;
  color: #64748b;
}

.profile-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #111827;
  font: inherit;
  font-size: 14px;
}

.profile-search ion-icon {
  font-size: 18px;
}

.profile-status-strip {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.profile-status-strip div {
  min-height: 62px;
  border: 1px solid #e5edf6;
  border-radius: 8px;
  display: grid;
  gap: 6px;
  place-items: center;
  background: #ffffff;
}

.profile-status-strip span {
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
}

.profile-status-strip strong {
  color: #111827;
  font-size: 18px;
  font-weight: 900;
}

.profile-panel {
  padding: 16px;
}

.profile-panel__head button {
  min-height: 32px;
  border: 0;
  border-radius: 8px;
  padding: 0 10px;
  background: #eef6ff;
  color: #0b63f6;
  font-size: 13px;
  font-weight: 800;
}

.profile-reminder-list,
.profile-menu-list {
  margin-top: 14px;
  display: grid;
  gap: 10px;
}

.profile-reminder-list button,
.profile-menu-list button {
  width: 100%;
  min-height: 60px;
  border: 1px solid #e5edf6;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 12px;
  background: #ffffff;
  color: #111827;
  text-align: left;
}

.profile-reminder-list span,
.profile-menu-list span:not(.profile-menu-icon) {
  min-width: 0;
  flex: 1;
  display: grid;
  gap: 5px;
}

.profile-reminder-list strong,
.profile-menu-list strong {
  color: #111827;
  font-size: 14px;
  font-weight: 800;
}

.profile-reminder-list small,
.profile-menu-list small {
  color: #64748b;
  font-size: 12px;
  line-height: 1.35;
}

.profile-reminder-list em {
  min-width: 30px;
  min-height: 28px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #eef6ff;
  color: #0b63f6;
  font-size: 12px;
  font-style: normal;
  font-weight: 900;
}

.profile-menu-icon {
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--menu-accent) 12%, #ffffff);
  color: var(--menu-accent);
  font-size: 20px;
}

.profile-menu-list button > ion-icon {
  flex: 0 0 auto;
  color: #94a3b8;
  font-size: 18px;
}

.profile-empty {
  padding: 22px 12px;
  color: #64748b;
  font-size: 13px;
  text-align: center;
}

.profile-logout-button {
  width: 100%;
  min-height: 44px;
  color: #b91c1c;
  border-color: #fecaca;
  background: #fff7f7;
  font-size: 15px;
  font-weight: 800;
}

.profile-sheet-mask {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 16px;
  background: rgba(15, 23, 42, 0.36);
}

.profile-security-panel {
  width: min(100%, 520px);
  max-height: calc(100vh - 52px);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 18px 42px rgba(15, 23, 42, 0.22);
  padding: 18px;
  overflow: auto;
}

.profile-security-panel header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.profile-security-panel header h2 {
  margin: 6px 0 0;
  color: #111827;
  font-size: 18px;
  line-height: 1.25;
}

.profile-security-panel header button {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #f1f5f9;
  color: #334155;
  font-size: 22px;
  line-height: 1;
}

.profile-detail-loading {
  margin-top: 16px;
  min-height: 80px;
  border: 1px solid #e5edf6;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #f8fafc;
  color: #64748b;
  font-size: 13px;
  font-weight: 800;
}

.profile-detail-body {
  margin-top: 16px;
}

.profile-detail-list {
  border: 1px solid #e5edf6;
  border-radius: 8px;
  overflow: hidden;
  background: #ffffff;
}

.profile-detail-row {
  min-height: 44px;
  padding: 10px 12px;
  display: grid;
  grid-template-columns: minmax(82px, 0.35fr) minmax(0, 1fr);
  gap: 12px;
  align-items: center;
}

.profile-detail-row + .profile-detail-row {
  border-top: 1px solid #eef3f8;
}

.profile-detail-row span {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.profile-detail-row strong {
  min-width: 0;
  color: #111827;
  font-size: 13px;
  font-weight: 850;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.profile-detail-form {
  margin-top: 14px;
}

.profile-security-form {
  margin-top: 16px;
  display: grid;
  gap: 12px;
}

.profile-security-form label {
  display: grid;
  gap: 7px;
}

.profile-security-form label span {
  color: #475569;
  font-size: 13px;
  font-weight: 800;
}

.profile-security-form input {
  width: 100%;
  height: 44px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  padding: 0 12px;
  background: #f8fafc;
  color: #111827;
  font: inherit;
  outline: 0;
}

.profile-security-form input:focus {
  border-color: #0b63f6;
  background: #ffffff;
}

.profile-security-message {
  margin: 0;
  color: #047857;
  font-size: 13px;
  line-height: 1.5;
}

.profile-security-message.is-error {
  color: #b91c1c;
}

.profile-security-submit {
  min-height: 44px;
  border: 0;
  border-radius: 8px;
  background: #0b63f6;
  color: #ffffff;
  font-size: 15px;
  font-weight: 900;
}

.profile-security-submit:disabled {
  opacity: 0.68;
}

@media (max-width: 360px) {
  .profile-shell {
    padding-inline: 10px;
  }

  .profile-status-strip {
    grid-template-columns: repeat(1, minmax(0, 1fr));
  }
}
</style>
