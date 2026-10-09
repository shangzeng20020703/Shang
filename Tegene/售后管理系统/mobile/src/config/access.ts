import { roleWorkbenchItems, workbenchItems, type WorkbenchShortcutItem } from './workbench'

export type AppRole = 'admin' | 'asset_admin' | 'hr' | 'manager' | 'finance' | 'employee'

/**
 * 移动端权限配置。
 *
 * 这是移动端“角色 -> 可见工作台入口 -> 可访问页面”的中心文件。
 * 任何移动端新功能要上线，通常都要先补这份配置，再补 router meta。
 */

export type WorkbenchItem = WorkbenchShortcutItem
export { workbenchItems }

export const mobileSupportedRoles: AppRole[] = ['admin', 'employee', 'manager', 'hr']

export const roleLabels: Record<AppRole, string> = {
  admin: '系统管理员',
  asset_admin: '行政管理员',
  hr: '售后管理员',
  manager: '项目经理',
  finance: '财务',
  employee: '售后人员',
}

export function normalizeRoles(input: unknown): AppRole[] {
  // 只保留移动端真正支持的角色，剔除未知值，并保证至少回落到 employee。
  const roles = Array.isArray(input) ? input : []
  const filtered = roles
    .map((item) => String(item).trim().toLowerCase())
    .filter((item): item is AppRole => mobileSupportedRoles.includes(item as AppRole))

  return filtered.length ? Array.from(new Set(filtered)) : ['employee']
}

export function primaryRole(roles: AppRole[]): AppRole {
  // 当用户兼多角色时，移动端用一个“主角色”决定 UI 文案和默认视角。
  if (roles.includes('admin')) return 'admin'
  if (roles.includes('asset_admin')) return 'asset_admin'
  if (roles.includes('hr')) return 'hr'
  if (roles.includes('manager')) return 'manager'
  if (roles.includes('finance')) return 'finance'
  return 'employee'
}

export function isRoleAllowed(roles: AppRole[], required?: AppRole[]) {
  // 路由和工作台都共用这套判断，逻辑保持完全一致。
  if (!required?.length) return true
  return required.some((role) => roles.includes(role))
}

export function roleWorkbench(roles: AppRole[]) {
  // 根据当前用户角色裁剪移动端首页卡片。
  return roleWorkbenchItems(roles)
}
