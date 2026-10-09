import { rememberLoginDestination } from '@/utils/wecom'
import { createRouter, createWebHistory } from '@ionic/vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { isRoleAllowed, type AppRole } from '@/config/access'

/**
 * 移动端路由总线。
 *
 * 和 Web 端不同，移动端页面更像“角色驱动的工作台”：
 * - `/login` 负责获取 token
 * - `/app/*` 负责承载 tab 和功能页面
 * - beforeEach 在进入页面前补拉用户资料并做角色拦截
 */

const allMobileRoles: AppRole[] = ['employee', 'manager', 'hr', 'finance', 'asset_admin']

const routes: Array<RouteRecordRaw> = [{path:'/',redirect:'/app/tabs/home'},{path:'/login',component:()=>import('@/pages/LoginPage.vue')},{path:'/app',component:()=>import('@/layouts/AppTabs.vue'),children:[{path:'',redirect:'/app/tabs/home'},
{path:'tabs/home',component:()=>import('@/pages/HomePage.vue'),meta:{breadcrumb:[{label:'首页'}]}},
{path:'tabs/workbench',component:()=>import('@/pages/WorkbenchPage.vue'),meta:{breadcrumb:[{label:'工作台'}]}},
{path:'tabs/approvals',component:()=>import('@/pages/ApprovalsPage.vue'),meta:{breadcrumb:[{label:'审批中心'}]}},
{path:'tabs/profile',component:()=>import('@/pages/ProfilePage.vue'),meta:{breadcrumb:[{label:'我的'}]}},
{path:'notifications',component:()=>import('@/pages/MessageCenterPage.vue'),meta:{breadcrumb:[{label:'通知'}]}},
{path:'attendance',component:()=>import('@/pages/AttendancePage.vue'),meta:{breadcrumb:[{label:'打卡'}]}},
{path:'approval/start',component:()=>import('@/pages/ApprovalStartPage.vue'),meta:{breadcrumb:[{label:'发起申请'}]}},
{path:'approval/:id',component:()=>import('@/pages/ApprovalDetailPage.vue'),meta:{breadcrumb:[{label:'审批详情'}]}},
{path:'people',component:()=>import('@/pages/PeoplePage.vue'),meta:{roles:['admin','hr','manager'],breadcrumb:[{label:'团队花名册'}]}},
{path:'messages',redirect:'/app/notifications'},
{path:'team-attendance',component:()=>import('@/pages/TeamAttendancePage.vue'),meta:{roles:['admin','hr','manager'],breadcrumb:[{label:'团队考勤'}]}},
{path:'field',component:()=>import('@/pages/MobileFieldPage.vue'),meta:{breadcrumb:[{label:'我的项目'}]}},
{path:'leave',redirect:to=>({path:'/app/approval/start',query:{...to.query,business_code:'leave',source:to.query.source||'leave_shortcut'}})},{path:'applications',redirect:'/app/tabs/approvals?segment=submitted'}]},{path:'/:pathMatch(.*)*',redirect:'/app/tabs/home'}]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach(async (to, from) => {
  if (!from.matched.length && to.path === '/app/tabs/workbench') return '/app/tabs/home'
  if (to.path === '/login' && (to.query.code || to.query.state)) return true
  const auth = useAuthStore()
  const requiresAuth = to.path !== '/login'

  if (!requiresAuth) {
    // 已登录用户再次进登录页时，直接送回移动端首页。
    if (auth.isLoggedIn && !auth.user) {
      try {
        await auth.fetchUser()
      } catch {
        auth.logout()
      }
    }
    if (auth.isLoggedIn && auth.user) return '/app/tabs/home'
    return true
  }

  // 任何业务页都要求先有 token。
  if (!auth.isLoggedIn) { rememberLoginDestination('mobile',to.fullPath); return '/login' }

  if (!auth.user) {
    try {
      await auth.fetchUser()
    } catch {
      auth.logout()
      rememberLoginDestination('mobile',to.fullPath)
      return '/login'
    }
  }

  const requiredRoles = to.meta.roles as AppRole[] | undefined
  // 移动端不展示“无权限”中间页，直接回首页，保持路径简单。
  if (requiredRoles && !isRoleAllowed(auth.roles, requiredRoles)) {
    return '/app/tabs/home'
  }

  return true
})

export default router
