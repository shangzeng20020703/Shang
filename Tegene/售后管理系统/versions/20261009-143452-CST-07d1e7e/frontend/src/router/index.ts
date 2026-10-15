import { createRouter, createWebHistory } from 'vue-router'
import { hasUsableWebAuthToken, getWebAuthRoles, isWebAuthSuperuser, hasWebManagementRoleSnapshot, clearWebAuthSession } from '@/utils/authSession'
import { rememberLoginDestination } from '@/utils/wecom'
import { useUserStore } from '@/stores/user'
const admin = ['admin','hr']
const router=createRouter({history:createWebHistory(),routes:[
{path:'/login',component:()=>import('@/views/LoginView.vue')},
{path:'/',component:()=>import('@/views/LayoutView.vue'),redirect:'/dashboard',children:[
{path:'dashboard',component:()=>import('@/views/FieldDashboard.vue'),meta:{title:'项目人员看板'}},
{path:'field',component:()=>import('@/views/FieldView.vue'),meta:{title:'项目库'}},
{path:'employees',component:()=>import('@/views/FieldPeople.vue'),meta:{title:'人员花名册',roles:admin}},
{path:'organization',component:()=>import('@/views/OrganizationView.vue'),meta:{title:'组织架构',roles:admin}},
{path:'attendance',component:()=>import('@/views/AttendanceView.vue'),meta:{title:'考勤管理',roles:admin}},
{path:'approval',component:()=>import('@/views/ApprovalView.vue'),meta:{title:'审批与模板'}},
{path:'audit',component:()=>import('@/views/FieldAudit.vue'),meta:{title:'操作日志',roles:admin}},
{path:'event-monitor',component:()=>import('@/views/EventMonitorView.vue'),meta:{title:'业务事件',roles:admin}},
]}, {path:'/:pathMatch(.*)*',redirect:'/dashboard'}]})
router.beforeEach(async to=>{
 if(to.path==='/login' && (to.query.code || to.query.state)) return true
 if(hasUsableWebAuthToken()){
  const user=useUserStore()
  try { await user.fetchUserInfo() } catch { user.logout() }
 }
 if(!hasUsableWebAuthToken() || !hasWebManagementRoleSnapshot()){
  if(to.path!=='/login') rememberLoginDestination('web',to.fullPath)
  clearWebAuthSession();return to.path==='/login'?true:'/login'
 }
 if(to.path==='/login')return '/dashboard'
 const roles=to.meta.roles as string[]|undefined
 if(roles && !isWebAuthSuperuser() && !getWebAuthRoles().some(r=>roles.includes(r)))return '/dashboard'
 return true
})
export default router
