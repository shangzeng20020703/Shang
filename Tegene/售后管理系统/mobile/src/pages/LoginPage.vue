<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page">
      <div class="mobile-shell login-shell">
        <section class="hero-card login-hero">
          <div class="login-hero__mark">S</div>
          <div>
            <div class="hero-card__eyebrow">SERVICE / FIELD</div>
            <div class="hero-card__title">售后管理系统</div>
            <div class="hero-card__desc">
              打卡、审批、假勤与消息集中处理。登录后会根据你的角色展示对应入口。
            </div>
          </div>
        </section>

        <section class="section-block solid-card login-card">
          <div class="login-card__head">
            <h2 class="page-section-title">登录</h2>
            <span>欢迎回来</span>
          </div>
          <p v-if="!policy" class="login-policy-note">{{ errorMessage || '正在获取登录方式…' }}</p>
          <div v-if="policy?.wecom_enabled">
            <ion-button expand="block" size="large" class="login-submit" :disabled="wecomLoading || !policy.configuration_ready" @click="handleWecom">
              {{ wecomLoading ? '正在验证身份…' : '企业微信登录' }}
            </ion-button>
            <p class="login-policy-note">使用本企业身份进入，未授权人员无法访问。</p>
          </div>
          <p v-if="errorMessage && !policy?.password_enabled" class="danger-text login-error">{{ errorMessage }}</p>
          <form v-if="policy?.password_enabled" class="login-form" @submit.prevent="handleLogin">
            <div class="login-form-list">
              <label class="login-field-card">
                <span class="login-field-card__label">账号或手机号</span>
                <input
                  v-model="phone"
                  type="text"
                  inputmode="text"
                  autocomplete="username"
                  placeholder="请输入账号或手机号"
                />
              </label>
              <div v-if="savedAccounts.length" class="account-history">
                <span>历史账号或手机号</span>
                <button
                  v-for="account in savedAccounts"
                  :key="account"
                  type="button"
                  :class="{ 'is-active': phone === account }"
                  @click="selectAccount(account)"
                >
                  {{ account }}
                </button>
              </div>
              <label class="login-field-card">
                <span class="login-field-card__label">密码</span>
                <input
                  v-model="password"
                  type="password"
                  autocomplete="current-password"
                  placeholder="请输入密码"
                />
              </label>
            </div>
            <label class="remember-row">
              <input v-model="rememberAccount" type="checkbox" />
              <span>保持登录并记住账号或手机号，长期免重复输入</span>
            </label>
            <p v-if="errorMessage" class="danger-text login-error">{{ errorMessage }}</p>
            <ion-button expand="block" size="large" class="login-submit" type="submit" :disabled="auth.loading">
              {{ auth.loading ? '登录中...' : '登录移动端' }}
            </ion-button>
          </form>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { IonButton, IonContent, IonPage } from '@ionic/vue'
import { useAuthStore } from '@/stores/auth'
import { loadLoginPolicy, startWecom, finishWecom, takeWecomCallback, consumeLoginDestination, type LoginPolicy } from '@/utils/wecom'

/**
 * 移动端登录页。
 *
 * 它比 Web 登录页更轻，只负责：
 * - 收集账号或手机号和密码
 * - 调用移动端 auth store
 * - 登录成功后进入 `/app/tabs/home`
 *
 * 角色分流不在这里做，而是在首页工作台和路由 meta 中处理。
 */

const callback = takeWecomCallback()
const policy = ref<LoginPolicy | null>(null)
const wecomLoading = ref(false)
const auth = useAuthStore()
const router = useRouter()

const phone = ref('')
const password = ref('')
const errorMessage = ref('')
const rememberAccount = ref(true)
const savedAccounts = ref<string[]>([])

const ACCOUNT_HISTORY_KEY = 'mobile_login_accounts'
const ACCOUNT_REMEMBER_KEY = 'mobile_login_remember'
const MAX_ACCOUNT_HISTORY = 5

function loadSavedAccounts() {
  try {
    const raw = localStorage.getItem(ACCOUNT_HISTORY_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    savedAccounts.value = Array.isArray(parsed)
      ? parsed.map((item) => String(item || '').trim()).filter(Boolean).slice(0, MAX_ACCOUNT_HISTORY)
      : []
  } catch {
    savedAccounts.value = []
  }
  rememberAccount.value = localStorage.getItem(ACCOUNT_REMEMBER_KEY) !== 'false'
  if (rememberAccount.value && !phone.value && savedAccounts.value[0]) {
    phone.value = savedAccounts.value[0]
  }
}

function saveAccountHistory(account: string) {
  const normalized = account.trim()
  if (!normalized) return
  const next = [normalized, ...savedAccounts.value.filter((item) => item !== normalized)].slice(0, MAX_ACCOUNT_HISTORY)
  savedAccounts.value = next
  localStorage.setItem(ACCOUNT_HISTORY_KEY, JSON.stringify(next))
  localStorage.setItem(ACCOUNT_REMEMBER_KEY, String(rememberAccount.value))
}

function clearAccountHistory() {
  savedAccounts.value = []
  localStorage.removeItem(ACCOUNT_HISTORY_KEY)
  localStorage.setItem(ACCOUNT_REMEMBER_KEY, 'false')
}

function selectAccount(account: string) {
  phone.value = account
  password.value = ''
  errorMessage.value = ''
}

async function handleLogin() {
  errorMessage.value = ''
  try {
    const normalizedAccount = phone.value.trim()
    await auth.login(normalizedAccount, password.value, rememberAccount.value)
    if (rememberAccount.value) {
      saveAccountHistory(normalizedAccount)
    } else {
      clearAccountHistory()
    }
    await router.replace(consumeLoginDestination('mobile','/app/tabs/home'))
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '登录失败，请检查账号或手机号或密码'
  }
}

async function handleWecom() {
  wecomLoading.value = true
  errorMessage.value = ''
  try { await startWecom('mobile') }
  catch (error: any) { errorMessage.value = error?.response?.data?.detail || error.message; wecomLoading.value = false }
}

onMounted(async () => {
  loadSavedAccounts()
  try {
    policy.value = await loadLoginPolicy()
    if (callback) {
      wecomLoading.value = true
      const data = await finishWecom('mobile', callback)
      await auth.acceptToken(data.access_token)
      await router.replace(consumeLoginDestination('mobile','/app/tabs/home'))
    }
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error.message || '登录服务不可用，请刷新重试'
  } finally { wecomLoading.value = false }
})
</script>

<style scoped>
.login-policy-note { color: #64748b; line-height: 1.8; font-size: 14px; }
.login-shell {
  padding-top: 18px;
}

.login-hero {
  position: relative;
  overflow: hidden;
  padding: 26px 26px 30px;
  border: 0;
  border-radius: 28px;
  background:
    radial-gradient(circle at 85% 18%, rgba(255, 255, 255, 0.28), transparent 28%),
    linear-gradient(135deg, #1f56dc 0%, #3479ef 58%, #5aa7ff 100%);
  color: #fff;
  display: grid;
  grid-template-columns: 54px 1fr;
  gap: 18px;
  align-items: start;
  box-shadow: 0 18px 36px rgba(32, 91, 222, 0.24);
}

.login-hero__mark {
  width: 54px;
  height: 54px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.28);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 900;
  letter-spacing: 0.08em;
}

.login-hero .hero-card__eyebrow {
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.login-hero .hero-card__title {
  margin-top: 10px;
  color: #fff;
  font-size: 28px;
  line-height: 1.2;
  font-weight: 900;
}

.login-hero .hero-card__desc {
  margin-top: 14px;
  max-width: 640px;
  color: rgba(255, 255, 255, 0.82);
  font-size: 15px;
  line-height: 1.8;
}

.login-card {
  margin-top: 28px;
  padding: 26px;
  border-radius: 28px;
  box-shadow: 0 18px 42px rgba(15, 23, 42, 0.08);
}

.login-card__head {
  margin-bottom: 22px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.login-card__head h2 {
  margin: 0;
}

.login-card__head span {
  color: #8b95a7;
  font-size: 13px;
  font-weight: 700;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.login-form-list {
  padding: 0;
  background: transparent;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.login-field-card {
  overflow: hidden;
  display: grid;
  grid-template-rows: 36px 56px;
  border: 1px solid #dfe7f3;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.login-field-card:focus-within {
  border-color: #3978f3;
  box-shadow:
    0 0 0 3px rgba(57, 120, 243, 0.14),
    0 12px 28px rgba(35, 83, 180, 0.1);
  transform: translateY(-1px);
}

.login-field-card__label {
  display: flex;
  align-items: center;
  padding: 0 18px;
  border-bottom: 1px solid #edf2f8;
  background: #fff;
  color: #26364d;
  font-size: 13px;
  font-weight: 850;
}

.login-field-card:focus-within .login-field-card__label {
  background: #f8fbff;
  color: #2f6eea;
}

.login-field-card input {
  width: 100%;
  height: 100%;
  border: 0;
  outline: 0;
  padding: 0 18px;
  background: #f3f7fc;
  color: #172033;
  font-size: 16px;
  font-weight: 650;
  appearance: none;
}

.login-field-card input::placeholder {
  color: #9aa7b8;
  font-weight: 500;
}

.account-history {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
  padding: 0 2px;
}

.account-history span {
  margin-right: 2px;
  color: #8b95a7;
  font-size: 12px;
  font-weight: 800;
}

.account-history button {
  min-height: 30px;
  border: 1px solid #d9e4f4;
  border-radius: 999px;
  padding: 0 12px;
  background: #fff;
  color: #506178;
  font-size: 12px;
  font-weight: 800;
}

.account-history button.is-active {
  border-color: #3978f3;
  background: #edf4ff;
  color: #2f6eea;
}

.remember-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #5f6f86;
  font-size: 13px;
  font-weight: 700;
}

.remember-row input {
  width: 18px;
  height: 18px;
  accent-color: #3479ef;
}

.login-error {
  margin: -4px 2px 0;
}

.login-submit {
  margin-top: 8px;
  --border-radius: 16px;
  --box-shadow: 0 12px 24px rgba(52, 93, 244, 0.28);
  min-height: 54px;
  font-weight: 900;
}

@media (max-width: 520px) {
  .login-shell {
    padding: 14px 14px 28px;
  }

  .login-hero {
    grid-template-columns: 1fr;
    padding: 24px 22px 28px;
  }

  .login-card {
    padding: 22px;
  }
}
</style>
