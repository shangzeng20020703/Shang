<template>
  <div class="login-page">
    <!-- Left Showcase Panel -->
    <div class="login-showcase">
      <div class="showcase-content">
        <!-- Decorative elements -->
        <div class="deco-circle deco-circle-1"></div>
        <div class="deco-circle deco-circle-2"></div>
        <div class="deco-circle deco-circle-3"></div>
        <div class="deco-dots">
          <span v-for="n in 25" :key="n" class="deco-dot"></span>
        </div>

        <div class="showcase-text">
          <h1 class="showcase-title">售后管理系统</h1>
          <div class="showcase-divider"></div>
          <p class="showcase-desc">
            项目人员、打卡与考勤管理
          </p>
        </div>
      </div>
      <div class="showcase-version">v1.0.0</div>
    </div>

    <!-- Right Login Form Panel -->
    <div class="login-form-panel">
      <div class="login-form-wrapper">
        <div class="login-form-header" :class="{ 'animate-in': mounted }">
          <h2 class="form-title">欢迎回来</h2>
        </div>

        <div v-if="!policy" class="login-policy-note">{{ authError || '正在获取登录方式…' }}</div>
        <div v-if="policy?.wecom_enabled" class="wecom-entry">
          <button class="login-button" :disabled="loading || !policy.configuration_ready" @click="handleWecom">
            {{ loading ? '正在验证身份…' : '企业微信登录' }}
          </button>
          <p class="login-policy-note">仅限已授权的本企业成员；手机企微内授权，电脑端扫码登录。</p>
        </div>
        <el-alert v-if="authError && policy" :title="authError" type="error" :closable="false" show-icon />
        <el-form v-if="policy?.password_enabled"
          ref="formRef"
          :model="loginForm"
          :rules="rules"
          class="login-form"
          :class="{ 'animate-in-delay': mounted }"
          @keyup.enter="handleLogin"
        >
          <el-form-item prop="phone">
            <div class="custom-input-wrapper">
              <span class="input-prefix">账号</span>
              <el-input
                v-model="loginForm.phone"
                placeholder="请输入账号或手机号"
                size="large"
                class="phone-input"
                maxlength="20"
              />
            </div>
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="loginForm.password"
              :type="showPassword ? 'text' : 'password'"
              placeholder="请输入密码"
              size="large"
              class="password-input"
            >
              <template #prefix>
                <el-icon class="input-icon"><Lock /></el-icon>
              </template>
              <template #suffix>
                <el-icon
                  class="password-toggle"
                  @click="showPassword = !showPassword"
                >
                  <View v-if="showPassword" />
                  <Hide v-else />
                </el-icon>
              </template>
            </el-input>
          </el-form-item>

          <div class="form-options">
            <el-checkbox v-model="rememberMe" label="记住账号" />
            <a href="javascript:void(0)" class="forgot-link" @click="ElMessage.info('请联系管理员重置密码')">忘记密码?</a>
          </div>

          <el-form-item class="login-btn-item">
            <button
              type="button"
              class="login-button"
              :class="{ 'is-loading': loading }"
              :disabled="loading"
              @click="handleLogin"
            >
              <span v-if="loading" class="btn-loading-spinner"></span>
              {{ loading ? '登录中...' : '登 录' }}
            </button>
          </el-form-item>
        </el-form>

        <div class="login-footer" :class="{ 'animate-in-delay-2': mounted }">
          售后管理系统 &copy; 2026
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock, View, Hide } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { getWebManagementHomePath, hasWebManagementAccess } from '@/utils/authSession'
import type { FormInstance, FormRules } from 'element-plus'
import { loadLoginPolicy, startWecom, finishWecom, takeWecomCallback, consumeLoginDestination, type LoginPolicy } from '@/utils/wecom'

/**
 * Web 登录页。
 *
 * 这里做的事情很纯粹：
 * - 采集账号和密码
 * - 调用 user store 登录
 * - 登录成功后按角色跳到默认首页
 *
 * 注意：
 * - “记住账号”只保存手机号，不保存密码或跨标签页登录态；
 * - 默认登录值已经移除，避免正式环境暴露测试账号。
 */

const callback = takeWecomCallback()
const policy = ref<LoginPolicy | null>(null)
const authError = ref('')
const router = useRouter()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const showPassword = ref(false)
const rememberMe = ref(false)
const mounted = ref(false)

const loginForm = reactive({
  phone: '',
  password: '',
})

const rules: FormRules = {
  phone: [
    { required: true, message: '请输入账号或手机号', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
  ],
}

onMounted(async () => {
  try {
    policy.value = await loadLoginPolicy()
    if (callback) {
      loading.value = true
      const data = await finishWecom('web', callback)
      await userStore.acceptToken(data.access_token)
      await router.replace(consumeLoginDestination('web',getHomePath()))
    }
  } catch (error: any) {
    authError.value = error?.response?.data?.detail || error.message || '登录服务不可用，请刷新重试'
  } finally { loading.value = false }

  const rememberedPhone = localStorage.getItem('aftersales_remembered_phone') || ''
  loginForm.phone = rememberedPhone
  rememberMe.value = Boolean(rememberedPhone)
  setTimeout(() => {
    mounted.value = true
  }, 100)
})

function getHomePath() {
  // 登录成功后的落点和 Web 路由守卫保持一致，避免出现角色判断分叉。
  return getWebManagementHomePath(
    (userStore.userInfo?.roles || []) as string[],
    Boolean(userStore.userInfo?.is_superuser),
  )
}

function canEnterManagement() {
  return hasWebManagementAccess(
    (userStore.userInfo?.roles || []) as string[],
    Boolean(userStore.userInfo?.is_superuser),
  )
}

async function handleWecom() {
  loading.value = true
  authError.value = ''
  try { await startWecom('web') }
  catch (error: any) { authError.value = error?.response?.data?.detail || error.message; loading.value = false }
}

async function handleLogin() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login(loginForm.phone, loginForm.password)
    if (!canEnterManagement()) {
      userStore.logout()
      ElMessage.warning('当前账号未开通管理端权限，请使用员工端入口')
      return
    }
    if (rememberMe.value) localStorage.setItem('aftersales_remembered_phone', loginForm.phone)
    else localStorage.removeItem('aftersales_remembered_phone')
    ElMessage.success('登录成功')
    // 登录成功后不固定跳 dashboard，而是按角色送往各自首页。
    router.push(consumeLoginDestination('web',getHomePath()))
  } catch (error: any) {
    const status = Number(error?.response?.status || 0)
    const detail = error?.response?.data?.detail
    const msg =
      status === 401 || status === 403 ? (detail || '登录失败，请检查账号和密码')
        : status >= 500 ? '登录服务暂时不可用，请稍后重试'
          : !error?.response ? '无法连接服务器，请检查网络或后端服务'
            : (detail || '登录失败，请稍后重试')
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-policy-note { color: #64748b; line-height: 1.8; margin: 16px 0; font-size: 14px; }
.wecom-entry { margin: 24px 0; }
.login-button:disabled { opacity: .6; cursor: wait; }
.login-page {
  display: flex;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}

/* ---- Left Showcase ---- */
.login-showcase {
  width: 55%;
  min-height: 100vh;
  background: linear-gradient(135deg, #1E1B4B 0%, #312E81 40%, #4F46E5 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
}

.showcase-content {
  position: relative;
  z-index: 2;
  text-align: center;
  padding: 40px;
}

.showcase-text {
  position: relative;
  z-index: 3;
}

.showcase-title {
  font-size: 48px;
  font-weight: 800;
  color: #FFFFFF;
  letter-spacing: 4px;
  margin-bottom: 12px;
  text-shadow: 0 2px 20px rgba(0, 0, 0, 0.2);
}

.showcase-divider {
  width: 60px;
  height: 3px;
  background: rgba(255, 255, 255, 0.3);
  border-radius: 2px;
  margin: 32px auto;
}

.showcase-desc {
  font-size: 15px;
  color: rgba(255, 255, 255, 0.45);
  margin: 0;
  letter-spacing: 1px;
}

.showcase-version {
  position: absolute;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 12px;
  color: rgba(255, 255, 255, 0.3);
  letter-spacing: 1px;
}

/* Decorative circles */
.deco-circle {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.deco-circle-1 {
  width: 400px;
  height: 400px;
  top: -100px;
  left: -100px;
  border-width: 2px;
  border-color: rgba(255, 255, 255, 0.06);
}

.deco-circle-2 {
  width: 300px;
  height: 300px;
  bottom: -80px;
  right: -60px;
  background: rgba(255, 255, 255, 0.03);
}

.deco-circle-3 {
  width: 200px;
  height: 200px;
  top: 15%;
  right: 10%;
  border-color: rgba(255, 255, 255, 0.05);
  background: rgba(255, 255, 255, 0.02);
}

/* Decorative dots grid */
.deco-dots {
  position: absolute;
  bottom: 20%;
  left: 10%;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  opacity: 0.4;
}

.deco-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.3);
}

/* ---- Right Form Panel ---- */
.login-form-panel {
  width: 45%;
  min-height: 100vh;
  background: #FFFFFF;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.login-form-wrapper {
  width: 100%;
  max-width: 400px;
}

.login-form-header {
  margin-bottom: 40px;
  opacity: 0;
  transform: translateY(20px);
  transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1);
}

.login-form-header.animate-in {
  opacity: 1;
  transform: translateY(0);
}

.form-title {
  font-size: 28px;
  font-weight: 700;
  color: #1E293B;
  margin: 0 0 8px 0;
}

/* Form animations */
.login-form {
  opacity: 0;
  transform: translateY(20px);
  transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.15s;
}

.login-form.animate-in-delay {
  opacity: 1;
  transform: translateY(0);
}

/* Custom account input with prefix */
.custom-input-wrapper {
  display: flex;
  align-items: center;
  width: 100%;
  border: 1px solid #E2E8F0;
  border-radius: 10px;
  overflow: hidden;
  transition: all 0.2s ease;
  background: #FFFFFF;
}

.custom-input-wrapper:focus-within {
  border-color: #4F46E5;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

.input-prefix {
  padding: 0 12px 0 16px;
  font-size: 15px;
  color: #64748B;
  font-weight: 500;
  border-right: 1px solid #E2E8F0;
  margin-right: -1px;
  height: 48px;
  display: flex;
  align-items: center;
  background: #F8FAFC;
  flex-shrink: 0;
}

.phone-input :deep(.el-input__wrapper) {
  box-shadow: none !important;
  border-radius: 0 !important;
  height: 48px;
  padding-left: 12px;
}

.phone-input :deep(.el-input__inner) {
  font-size: 15px;
  height: 48px;
}

/* Password input */
.password-input :deep(.el-input__wrapper) {
  height: 48px;
  border-radius: 10px !important;
  box-shadow: 0 0 0 1px #E2E8F0 inset !important;
  transition: all 0.2s ease !important;
}

.password-input :deep(.el-input__wrapper):hover {
  box-shadow: 0 0 0 1px #C7D2FE inset !important;
}

.password-input :deep(.el-input__wrapper).is-focus {
  box-shadow: 0 0 0 1px #4F46E5 inset, 0 0 0 3px rgba(79, 70, 229, 0.1) !important;
}

.password-input :deep(.el-input__inner) {
  font-size: 15px;
  height: 48px;
}

.input-icon {
  color: #94A3B8;
  font-size: 18px;
}

.password-toggle {
  cursor: pointer;
  color: #94A3B8;
  font-size: 18px;
  transition: color 0.2s;
}

.password-toggle:hover {
  color: #4F46E5;
}

/* Form options row */
.form-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.form-options :deep(.el-checkbox__label) {
  color: #64748B;
  font-size: 14px;
}

.form-options :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: #4F46E5;
  border-color: #4F46E5;
}

.forgot-link {
  font-size: 14px;
  color: #4F46E5;
  font-weight: 500;
  transition: color 0.2s;
}

.forgot-link:hover {
  color: #6366F1;
}

/* Login button - gradient custom button */
.login-btn-item {
  margin-top: 8px;
}

.login-button {
  width: 100%;
  height: 48px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #4F46E5 0%, #6366F1 100%);
  color: #FFFFFF;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 2px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  position: relative;
  overflow: hidden;
}

.login-button::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, #6366F1 0%, #818CF8 100%);
  opacity: 0;
  transition: opacity 0.25s ease;
}

.login-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 25px -5px rgba(79, 70, 229, 0.4);
}

.login-button:hover:not(:disabled)::before {
  opacity: 1;
}

.login-button:active:not(:disabled) {
  transform: translateY(0);
}

.login-button:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.login-button span,
.login-button .btn-loading-spinner {
  position: relative;
  z-index: 1;
}

.btn-loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #FFFFFF;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Footer */
.login-footer {
  text-align: center;
  margin-top: 48px;
  font-size: 13px;
  color: #CBD5E1;
  opacity: 0;
  transform: translateY(10px);
  transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.3s;
}

.login-footer.animate-in-delay-2 {
  opacity: 1;
  transform: translateY(0);
}

/* Form item spacing */
.login-form :deep(.el-form-item) {
  margin-bottom: 22px;
}

.login-form :deep(.el-form-item__error) {
  padding-top: 4px;
}

/* ---- Responsive ---- */
@media (max-width: 768px) {
  .login-showcase {
    display: none;
  }

  .login-form-panel {
    width: 100%;
    padding: 24px;
  }

  .login-form-wrapper {
    max-width: 360px;
  }

  .form-title {
    font-size: 24px;
  }
}

@media (max-width: 1024px) and (min-width: 769px) {
  .login-showcase {
    width: 45%;
  }

  .login-form-panel {
    width: 55%;
  }

  .showcase-title {
    font-size: 36px;
  }
}
</style>
