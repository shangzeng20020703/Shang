import {
  briefcaseOutline,
  buildOutline,
  cashOutline,
  cubeOutline,
  documentTextOutline,
  fileTrayFullOutline,
  locationSharp,
  megaphoneOutline,
  peopleOutline,
  statsChartOutline,
  walletOutline,
} from 'ionicons/icons'
import type { AppRole } from './access'

export type ShortcutCategory = 'daily' | 'approval' | 'personal' | 'team' | 'admin'

export interface WorkbenchCatalogItem {
  title: string
  caption: string
  icon: string
  path: string
  accent: string
  roles: AppRole[]
  shortcutCategory: ShortcutCategory
}

export interface WorkbenchShortcutItem extends WorkbenchCatalogItem {}

export interface WorkbenchCatalogSection {
  title: string
  kicker: string
  tone: 'management' | 'tools'
  items: WorkbenchCatalogItem[]
}

const allMobileRoles: AppRole[] = ['admin', 'employee', 'manager', 'hr']

export const workbenchSections: WorkbenchCatalogSection[] = [
  {
    title: '员工自助',
    kicker: '高频',
    tone: 'management',
    items: [
      { title: '打卡', caption: '上下班与外出', icon: locationSharp, path: '/app/attendance', accent: '#0f766e', roles: allMobileRoles, shortcutCategory: 'daily' },
      { title: '审批', caption: '发起售后与考勤流程', icon: fileTrayFullOutline, path: '/app/approval/start', accent: '#0b63f6', roles: allMobileRoles, shortcutCategory: 'approval' },
      { title: '我发起的', caption: '进度与记录', icon: documentTextOutline, path: '/app/tabs/approvals?segment=submitted', accent: '#1d4ed8', roles: allMobileRoles, shortcutCategory: 'approval' },
      { title: '我的项目', caption: '参与项目与打卡规则', icon: briefcaseOutline, path: '/app/field', accent: '#0369a1', roles: allMobileRoles, shortcutCategory: 'daily' },
      { title: '请假', caption: '假期与申请', icon: fileTrayFullOutline, path: '/app/leave', accent: '#7c3aed', roles: allMobileRoles, shortcutCategory: 'approval' },
      { title: '通知', caption: '提醒与待办', icon: megaphoneOutline, path: '/app/notifications', accent: '#b45309', roles: allMobileRoles, shortcutCategory: 'daily' },
    ],
  },
  {
    title: '组织管理',
    kicker: '管理',
    tone: 'tools',
    items: [
      { title: '团队花名册', caption: '员工与组织', icon: peopleOutline, path: '/app/people', accent: '#0369a1', roles: ['admin', 'hr', 'manager'], shortcutCategory: 'team' },
      { title: '团队考勤', caption: '异常与统计', icon: statsChartOutline, path: '/app/team-attendance', accent: '#047857', roles: ['admin', 'hr', 'manager'], shortcutCategory: 'team' },
    ],
  },
]

export const workbenchItems: WorkbenchShortcutItem[] = workbenchSections
  .flatMap((section) => section.items)

export function roleWorkbenchItems(roles: AppRole[]) {
  return workbenchItems.filter((item) => item.roles.some((role) => roles.includes(role)))
}
