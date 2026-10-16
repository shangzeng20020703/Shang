import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { post, get } from '@/utils/request'
import {
  clearWebAuthSession,
  getWebAuthToken,
  setWebAuthRoleSnapshot,
  setWebAuthToken,
} from '@/utils/authSession'

/**
 * Web 管理端用户状态仓库。
 *
 * 这个 store 只处理“当前登录人”相关的数据：
 * - token
 * - 当前用户资料
 * - 当前用户角色集合
 *
 * 设计上把“登录成功后还要继续拉 /auth/me 和 /rbac 角色”分成两步，
 * 这样登录接口只负责认证，用户画像和角色归属由后续请求补全。
 */

interface UserInfo {
  id: number
  name: string
  phone: string
  email: string
  department: string
  position: string
  role?: string
  roles?: string[]
  is_superuser?: boolean
}

interface RoleRecord {
  role_name: string
  is_active: boolean
}

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(getWebAuthToken())
  const userInfo = ref<UserInfo | null>(null)

  const isLoggedIn = computed(() => !!token.value)

  async function login(phone: string, password: string) {
    // 登录接口只返回 token，拿到 token 后必须继续 fetchUserInfo，
    // 否则路由守卫和菜单无法知道当前用户的角色。
    const data = await post<{ access_token: string; token_type: string }>('/auth/management-login', {
      phone,
      password,
    }, {
      // 登录页会自行消费认证失败与服务异常，避免把这类已处理错误重复灌进控制台自动修复队列。
      skipErrorReport: true,
      skipNetworkErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    await acceptToken(data.access_token)
  }

  async function acceptToken(value: string) {
    token.value = value
    setWebAuthToken(value)
    try { await fetchUserInfo() } catch (error) { logout(); throw error }
  }

  async function fetchUserInfo() {
    const data = await get<UserInfo>('/auth/management-session', undefined, {
      // /auth/me 主要用于登录后和刷新后的会话补水；失败时由调用方决定是否降级，
      // 不应把后端鉴权/档案异常直接作为页面控制台错误灌入自动修复队列。
      skipErrorReport: true,
      skipNetworkErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    let roles: string[] = []

    try {
      // 角色归属不内嵌在 /auth/me 中，而是单独走 RBAC 接口。
      const roleRecords = await get<RoleRecord[]>(`/rbac/employees/${data.id}/roles`)
      roles = Array.from(
        new Set(
          roleRecords
            .filter((item) => item.is_active)
            .map((item) => String(item.role_name || '').trim().toLowerCase())
            .filter(Boolean),
        ),
      )
    } catch {
      roles = []
    }

    const normalizedRoles =
      data.is_superuser ? ['admin']
        : roles.length ? roles
          : ['employee']

    // 挑一个“主角色”放到 role 字段里，兼容仍按单角色读取的旧页面。
    const role =
      data.is_superuser ? 'admin'
        : normalizedRoles.includes('admin') ? 'admin'
          : normalizedRoles.includes('asset_admin') ? 'asset_admin'
            : normalizedRoles.includes('hr') ? 'hr'
              : normalizedRoles.includes('manager') ? 'manager'
                : normalizedRoles.includes('finance') ? 'finance'
                  : normalizedRoles[0] || 'employee'

    userInfo.value = { ...data, role, roles: normalizedRoles }
    setWebAuthRoleSnapshot(role, normalizedRoles, Boolean(data.is_superuser))
  }

  function logout() {
    // 清掉所有和登录态相关的本地快照，避免下个用户继承上一个人的角色缓存。
    token.value = ''
    userInfo.value = null
    clearWebAuthSession()
  }

  return {
    token,
    userInfo,
    isLoggedIn,
    login,
    acceptToken,
    logout,
    fetchUserInfo,
  }
})
