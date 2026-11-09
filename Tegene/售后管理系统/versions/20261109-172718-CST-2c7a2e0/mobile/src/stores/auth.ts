import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { get, post } from '@/utils/request'
import { normalizeRoles, primaryRole, type AppRole } from '@/config/access'
import { clearMobileAuthToken, getMobileAuthToken, setMobileAuthToken } from '@/utils/authSession'

/**
 * 移动端认证仓库。
 *
 * 作用和 Web 端 user store 类似，但目标更聚焦：
 * - 保存移动端 token
 * - 拉取当前用户资料
 * - 把后端角色记录归一化成移动端可识别角色集合
 */

interface UserInfo {
  id: number
  name: string
  phone: string
  email?: string
  department?: string
  department_name?: string
  company?: string
  company_name?: string
  position?: string
  status?: string
  role?: AppRole
  roles?: AppRole[]
  is_superuser?: boolean
}

interface RoleRecord {
  role_name: string
  is_active: boolean
}

export const useAuthStore = defineStore('mobile-auth', () => {
  const token = ref(getMobileAuthToken())
  const user = ref<UserInfo | null>(null)
  const loading = ref(false)

  const isLoggedIn = computed(() => Boolean(token.value))
  const roles = computed<AppRole[]>(() => user.value?.roles || ['employee'])
  const displayRole = computed(() => primaryRole(roles.value))

  async function login(phone: string, password: string, rememberMe = true) {
    loading.value = true
    try {
      // 先拿 token，再补用户资料；移动端也遵循和 Web 一样的两段式登录。
      const data = await post<{ access_token: string }>('/auth/login', {
        phone,
        password,
        remember_me: rememberMe,
      })
      await acceptToken(data.access_token, rememberMe)
    } finally {
      loading.value = false
    }
  }

  async function acceptToken(value: string, rememberMe = false) {
    token.value = value
    setMobileAuthToken(value, rememberMe)
    try { await fetchUser() } catch (error) { logout(); throw error }
  }

  async function fetchUser() {
    const profile = await get<UserInfo>('/auth/me')
    let roleNames: AppRole[] = []

    try {
      // 移动端并不相信本地缓存角色，每次以服务端角色记录为准重新归一化。
      const roleRecords = await get<RoleRecord[]>(`/rbac/employees/${profile.id}/roles`)
      roleNames = normalizeRoles(
        roleRecords
          .filter((item) => item.is_active)
          .map((item) => item.role_name),
      )
    } catch {
      roleNames = ['employee']
    }

    // 管理员沿用原移动工作台的管理入口，权限仍由后端校验。
    const normalizedRoles: AppRole[] = profile.is_superuser ? ['admin'] : roleNames
    user.value = {
      ...profile,
      roles: normalizedRoles,
      role: primaryRole(normalizedRoles),
      department: profile.department || profile.department_name,
      company: profile.company || profile.company_name,
    }
  }

  function logout() {
    // 移动端只清自己的 token，不碰 Web 端的登录态。
    token.value = ''
    user.value = null
    clearMobileAuthToken()
  }

  return {
    token,
    user,
    loading,
    isLoggedIn,
    roles,
    displayRole,
    login,
    acceptToken,
    fetchUser,
    logout,
  }
})
