import { computed } from 'vue'
import { useUserStore } from '@/stores/user'

const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员',
  asset_admin: '行政管理员',
  hr: '售后管理员',
  manager: '项目经理',
  finance: '财务',
  employee: '售后人员',
}

export function useUserAccess() {
  const userStore = useUserStore()

  const isSuperuser = computed(() => Boolean((userStore.userInfo as any)?.is_superuser))

  const roles = computed<string[]>(() => {
    const rawRoles = [
      ...(((userStore.userInfo as any)?.roles || []) as string[]),
      String((userStore.userInfo as any)?.role || ''),
    ]
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean)

    return Array.from(new Set(rawRoles))
  })

  const primaryRole = computed(() => {
    if (isSuperuser.value) return 'admin'
    if (roles.value.includes('admin')) return 'admin'
    if (roles.value.includes('asset_admin')) return 'asset_admin'
    if (roles.value.includes('hr')) return 'hr'
    if (roles.value.includes('manager')) return 'manager'
    if (roles.value.includes('finance')) return 'finance'
    return roles.value[0] || 'employee'
  })

  const primaryRoleLabel = computed(() => ROLE_LABELS[primaryRole.value] || '员工')

  function hasAnyRole(...requiredRoles: string[]) {
    if (isSuperuser.value) return true
    return requiredRoles.some((role) => roles.value.includes(role.toLowerCase()))
  }

  return {
    isSuperuser,
    roles,
    primaryRole,
    primaryRoleLabel,
    hasAnyRole,
  }
}
