<template>
  <div
    class="layout-wrapper"
    :class="{
      'layout-wrapper--mobile': isMobile,
      'layout-wrapper--landscape-compact': isLandscapeCompact,
    }"
  >
    <!-- Sidebar -->
    <aside
      class="layout-sidebar"
      :class="{ 'is-collapsed': isCollapsed, 'is-mobile-open': isMobile && mobileSidebarOpen }"
    >
      <!-- Logo Area -->
      <div class="sidebar-logo">
        <div class="logo-badge">售后</div>
        <transition name="fade">
          <span v-if="!isCollapsed" class="logo-text">售后管理系统</span>
        </transition>
      </div>

      <!-- Navigation Menu -->
      <nav class="sidebar-nav">
        <template v-for="group in navigationGroups" :key="group.title">
          <div class="nav-group">
            <div v-if="!isCollapsed" class="nav-group-label">{{ group.title }}</div>
            <div v-for="item in group.items" :key="item.path" class="nav-item" :class="{ 'is-active': isActive(item.path) }" role="button" tabindex="0" @click="navigateTo(item.path)" @keydown.enter="navigateTo(item.path)">
              <el-icon :size="20"><component :is="item.icon" /></el-icon>
              <span v-if="!isCollapsed" class="nav-item-text">{{ item.label }}</span>
              <el-tooltip v-if="isCollapsed" :content="item.label" placement="right" :show-after="200"><span class="nav-tooltip-trigger"></span></el-tooltip>
            </div>
          </div>
          <div class="nav-divider"></div>
        </template>
      </nav>

      <!-- Sidebar Bottom -->
      <div class="sidebar-bottom">
        <div class="sidebar-user" v-if="!isCollapsed">
          <div class="user-avatar">
            {{ userInitial }}
          </div>
          <div class="user-info">
            <div class="user-name">{{ userStore.userInfo?.name || '用户' }}</div>
            <div class="user-role">{{ primaryRoleLabel }}</div>
          </div>
        </div>
        <div
          v-if="!isMobile"
          class="collapse-toggle"
          @click="isCollapsed = !isCollapsed"
        >
          <el-icon :size="18">
            <Fold v-if="!isCollapsed" />
            <Expand v-else />
          </el-icon>
        </div>
      </div>
    </aside>

    <!-- Main Area -->
    <div class="layout-main-area" :class="{ 'sidebar-collapsed': isCollapsed }">
      <!-- Header -->
      <header class="layout-header">
        <div class="header-left">
          <button
            v-if="isMobile"
            type="button"
            class="mobile-menu-toggle"
            aria-label="打开导航菜单"
            @click="toggleMobileSidebar"
          >
            <el-icon :size="20"><Operation /></el-icon>
          </button>
          <div v-if="isMobile" class="mobile-page-meta">
            <span class="mobile-page-label">售后管理系统</span>
            <strong class="mobile-page-title">{{ currentPageTitle || '首页' }}</strong>
          </div>
          <el-breadcrumb v-else separator="/">
            <el-breadcrumb-item :to="{ path: homePath }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentPageTitle">{{ currentPageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <div class="header-action" title="手机端" role="button" tabindex="0" @click="openMobile()" @keydown.enter="openMobile()"><el-icon :size="20"><Cellphone /></el-icon></div>
          <el-badge :value="unreadCount > 0 ? unreadCount : ''" :max="99" class="notif-badge">
            <div class="header-action notification-btn" title="通知" @click="openNotifDrawer">
              <el-icon :size="20"><Bell /></el-icon>
            </div>
          </el-badge>
          <el-dropdown trigger="click" @command="handleCommand">
            <div class="header-user">
              <div class="header-avatar">{{ userInitial }}</div>
              <span class="header-username">{{ userStore.userInfo?.name || '用户' }}</span>
              <el-icon class="header-arrow"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>
                  个人设置
                </el-dropdown-item>
                <el-dropdown-item command="password">
                  <el-icon><Lock /></el-icon>
                  修改密码
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- Content -->
      <main class="layout-content">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>

    <div
      v-if="isMobile && mobileSidebarOpen"
      class="layout-mobile-mask"
      @click="closeMobileSidebar"
    ></div>

    <!-- Notification Drawer -->
    <el-drawer
      v-model="notifDrawerVisible"
      title="消息通知"
      direction="rtl"
      :size="isMobile ? '100%' : '360px'"
      :show-close="true"
    >
      <div class="notif-header-actions">
        <span class="notif-unread-info">{{ unreadCount }} 条未读</span>
        <el-button link type="primary" @click="markAllRead">全部已读</el-button>
      </div>
      <div v-loading="notifLoading">
        <div
          v-for="n in notifications"
          :key="n.id"
          class="notif-item"
          :class="{ 'notif-item--unread': !n.is_read }"
          @click="markRead(n.id)"
        >
          <div v-if="!n.is_read" class="notif-item-dot"></div>
          <div class="notif-item-content">
            <div class="notif-item-title">{{ n.title }}</div>
            <div v-if="n.content" class="notif-item-body">{{ n.content }}</div>
            <div class="notif-item-time">{{ formatTime(n.created_at) }}</div>
          </div>
        </div>
        <el-empty
          v-if="!notifLoading && notifications.length === 0"
          description="暂无通知"
          :image-size="80"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  DataBoard,
  OfficeBuilding,
  User,
  Plus,
  Clock,
  Calendar,
  TrendCharts,
  Money,
  Stamp,
  Connection,
  Tickets,
  Aim,
  Reading,
  PieChart,
  Promotion,
  Grid,
  Fold,
  Expand,
  Operation,
  SwitchButton,
  Search,
  Bell,
  ArrowDown,
  Lock,
  Cellphone,
} from '@element-plus/icons-vue'
import { useResponsiveLayout } from '@/composables/useResponsiveLayout'
import { useUserStore } from '@/stores/user'
import { useUserAccess } from '@/composables/useUserAccess'
import { getWebManagementHomePath } from '@/utils/authSession'
import { get, post } from '@/utils/request'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { primaryRoleLabel, roles, isSuperuser, hasAnyRole } = useUserAccess()
const { isMobile, isLandscapeCompact } = useResponsiveLayout()

const isCollapsed = ref(false)
const mobileSidebarOpen = ref(false)

// ---- Notification center ----
const unreadCount = ref(0)
const notifDrawerVisible = ref(false)
const notifications = ref<any[]>([])
const notifLoading = ref(false)

function syncUnreadCount(items: any[]) {
  unreadCount.value = items.filter(item => !item?.is_read).length
}

async function fetchNotifications() {
  if (!userStore.userInfo) {
    notifications.value = []
    unreadCount.value = 0
    return
  }
  notifLoading.value = true
  try {
    const res: any = await get('/notifications', { page: 1, page_size: 20 })
    notifications.value = res.items || []
    const count = await get<{count:number}>('/notifications/unread-count')
    unreadCount.value = count.count
  } catch {
    unreadCount.value = 0
  } finally {
    notifLoading.value = false
  }
}

async function markAllRead() {
  if (!userStore.userInfo) return
  await post('/notifications/read-all', {})
  unreadCount.value = 0
  notifications.value = notifications.value.map(n => ({ ...n, is_read: true }))
}

async function markRead(id: number) {
  if (!userStore.userInfo) return
  await post(`/notifications/${id}/read`, {})
  const n = notifications.value.find(x => x.id === id)
  if (n && !n.is_read) {
    n.is_read = true
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  }
}

function openNotifDrawer() {
  if (!userStore.userInfo) return
  notifDrawerVisible.value = true
  fetchNotifications()
}

function formatTime(dt: string) {
  if (!dt) return ''
  const d = new Date(dt)
  const now = new Date()
  const diff = Math.floor((now.getTime() - d.getTime()) / 1000)
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

const currentPageTitle = computed(() => {
  return (route.meta?.title as string) || ''
})

const userInitial = computed(() => {
  const name = userStore.userInfo?.name || '用'
  return name.charAt(0)
})

const homePath = computed(() => {
  return getWebManagementHomePath(roles.value, isSuperuser.value)
})

const canAccessDashboard = computed(() => hasAnyRole('admin', 'hr'))
const canAccessAnalytics = computed(() => hasAnyRole('admin', 'hr'))
const canAccessPeopleAdmin = computed(() => hasAnyRole('admin', 'hr', 'manager'))
const canAccessOrganization = computed(() => hasAnyRole('admin', 'hr'))
const canAccessEmployeeDirectory = computed(() => hasAnyRole('admin', 'hr', 'manager'))
const canAccessRecruitment = computed(() => hasAnyRole('admin', 'hr'))
const canAccessAttendance = computed(() => hasAnyRole('admin', 'hr', 'manager'))
const canAccessPerformance = computed(() => hasAnyRole('admin', 'hr', 'manager'))
const canAccessTalentSuite = computed(() => hasAnyRole('admin', 'hr'))
const canAccessPayroll = computed(() => hasAnyRole('admin', 'hr', 'finance'))
const canAccessApproval = computed(() => hasAnyRole('admin', 'hr', 'manager', 'finance', 'asset_admin'))
const canAccessEventMonitor = computed(() => hasAnyRole('admin', 'hr'))
const canAccessAdmin = computed(() => hasAnyRole('admin'))
const canAccessFixedAssets = computed(() => hasAnyRole('admin', 'asset_admin'))

const navigationGroups = computed(() => {
  const admin = hasAnyRole('admin', 'hr')
  return [
    { title: '工作台', items: [{ path:'/dashboard', label:'项目人员看板', icon:DataBoard }, { path:'/field', label:'项目库', icon:Connection }] },
    { title: '人员管理', items: admin ? [{ path:'/organization', label:'组织架构', icon:OfficeBuilding }, { path:'/employees', label:'人员花名册', icon:User }] : [] },
    { title: '考勤管理', items: admin ? [{ path:'/attendance', label:'考勤管理', icon:Clock }] : [] },
    { title: '流程与管理', items: [{ path:'/approval', label:'审批管理', icon:Stamp }, ...(admin ? [{ path:'/audit', label:'操作日志', icon:Tickets }] : [])] },
  ].filter(group => group.items.length)
})

function isActive(path: string): boolean {
  if (path === '/employees') {
    return route.path.startsWith('/employees')
  }
  if (path === '/fixed-assets') {
    return route.path.startsWith('/fixed-assets')
  }
  return route.path === path
}

function navigateTo(path: string) {
  if (isMobile.value) {
    mobileSidebarOpen.value = false
  }
  router.push(path)
}

function toggleMobileSidebar() {
  mobileSidebarOpen.value = !mobileSidebarOpen.value
}

function closeMobileSidebar() {
  mobileSidebarOpen.value = false
}

function openMobile(path = '') { window.open((import.meta.env.DEV ? 'http://127.0.0.1:5187/mobile' : '/mobile') + path, '_blank', 'noopener') }

function handleCommand(command: string) {
  switch (command) {
    case 'logout':
      userStore.logout()
      router.push('/login')
      break
    case 'profile':
      openMobile('/app/tabs/profile')
      break
    case 'password':
      openMobile('/app/tabs/profile?settings=security')
      break
  }
}

onMounted(async () => {
  if (userStore.isLoggedIn && !userStore.userInfo) {
    try {
      await userStore.fetchUserInfo()
    } catch {
      // Token may be expired; interceptor will handle redirect
    }
  }
})

watch(() => userStore.userInfo?.id, () => { void fetchNotifications() }, { immediate: true })

watch(() => route.fullPath, () => {
  closeMobileSidebar()
})

watch(isMobile, (mobile) => {
  if (mobile) {
    isCollapsed.value = false
  } else {
    closeMobileSidebar()
  }
}, { immediate: true })
</script>

<style scoped>
/* ---- Layout Wrapper ---- */
.layout-wrapper {
  display: flex;
  height: 100vh;
  height: 100dvh;
  min-height: 100vh;
  min-height: 100dvh;
  overflow: hidden;
  background: #F8FAFC;
  position: relative;
}

/* ---- Sidebar ---- */
.layout-sidebar {
  width: 240px;
  height: 100vh;
  height: 100dvh;
  background: linear-gradient(180deg, #1E1B4B 0%, #312E81 100%);
  display: flex;
  flex-direction: column;
  transition: width 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  flex-shrink: 0;
  overflow: hidden;
  position: relative;
  z-index: 100;
}

.layout-sidebar.is-collapsed {
  width: 72px;
}

/* Logo */
.sidebar-logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 16px;
  flex-shrink: 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.logo-badge {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #818CF8, #6366F1);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #FFFFFF;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 1px;
  flex-shrink: 0;
}

.logo-text {
  font-size: 17px;
  font-weight: 700;
  color: #FFFFFF;
  white-space: nowrap;
  letter-spacing: 1px;
}

/* Navigation */
.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 12px 12px 8px;
}

.sidebar-nav::-webkit-scrollbar {
  width: 0;
}

.nav-group {
  margin-bottom: 4px;
}

.nav-group-label {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.35);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  padding: 12px 12px 6px;
  white-space: nowrap;
}

.nav-item {
  display: flex;
  align-items: center;
  height: 44px;
  padding: 0 12px;
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.65);
  cursor: pointer;
  transition: all 0.15s ease;
  gap: 12px;
  position: relative;
  margin-bottom: 2px;
  white-space: nowrap;
}

.nav-item .el-icon {
  opacity: 0.7;
  flex-shrink: 0;
  transition: opacity 0.15s ease;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.05);
  color: rgba(255, 255, 255, 0.9);
}

.nav-item:hover .el-icon {
  opacity: 1;
}

.nav-item.is-active {
  background: rgba(255, 255, 255, 0.1);
  color: #FFFFFF;
}

.nav-item.is-active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: #818CF8;
  border-radius: 0 3px 3px 0;
}

.nav-item.is-active .el-icon {
  opacity: 1;
}

.nav-item-text {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}

.nav-tooltip-trigger {
  position: absolute;
  inset: 0;
}

.nav-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.06);
  margin: 4px 12px;
}

/* Collapsed sidebar: center icons */
.is-collapsed .nav-item {
  justify-content: center;
  padding: 0;
}

.is-collapsed .nav-group-label {
  display: none;
}

.is-collapsed .sidebar-logo {
  justify-content: center;
  padding: 0;
  gap: 0;
}

/* Sidebar Bottom */
.sidebar-bottom {
  padding: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

.sidebar-user {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: 8px;
  margin-bottom: 8px;
}

.user-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, #818CF8, #6366F1);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #FFFFFF;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}

.user-info {
  overflow: hidden;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  color: #FFFFFF;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-role {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.4);
  margin-top: 2px;
  white-space: nowrap;
}

.collapse-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 36px;
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.5);
  cursor: pointer;
  transition: all 0.15s ease;
}

.collapse-toggle:hover {
  background: rgba(255, 255, 255, 0.05);
  color: rgba(255, 255, 255, 0.8);
}

/* ---- Main Area ---- */
.layout-main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  transition: margin-left 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

/* ---- Header ---- */
.layout-header {
  height: 60px;
  background: #FFFFFF;
  border-bottom: 1px solid #F1F5F9;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
  z-index: 50;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-action {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  color: #64748B;
  cursor: pointer;
  transition: all 0.15s ease;
  position: relative;
}

.header-action:hover {
  background: #F1F5F9;
  color: #4F46E5;
}

.notification-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  min-width: 18px;
  height: 18px;
  border-radius: 9px;
  background: #EF4444;
  color: #FFFFFF;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  border: 2px solid #FFFFFF;
  line-height: 1;
}

.header-user {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.15s ease;
  margin-left: 8px;
}

.header-user:hover {
  background: #F1F5F9;
}

.header-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, #4F46E5, #6366F1);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #FFFFFF;
  font-size: 13px;
  font-weight: 600;
}

.header-username {
  font-size: 14px;
  font-weight: 500;
  color: #1E293B;
}

.header-arrow {
  color: #94A3B8;
  font-size: 12px;
}

/* ---- Content Area ---- */
.layout-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  background: #F8FAFC;
}

.mobile-menu-toggle {
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 10px;
  background: #F1F5F9;
  color: #334155;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.mobile-page-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mobile-page-label {
  font-size: 11px;
  color: #94A3B8;
  line-height: 1.2;
}

.mobile-page-title {
  color: #0F172A;
  font-size: 15px;
  line-height: 1.2;
}

.layout-mobile-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.38);
  z-index: 90;
}

.layout-wrapper--landscape-compact .layout-sidebar {
  width: 72px;
}

.layout-wrapper--landscape-compact .sidebar-logo {
  height: 56px;
  justify-content: center;
  padding: 0;
  gap: 0;
}

.layout-wrapper--landscape-compact .logo-text,
.layout-wrapper--landscape-compact .nav-group-label,
.layout-wrapper--landscape-compact .nav-item-text,
.layout-wrapper--landscape-compact .sidebar-user,
.layout-wrapper--landscape-compact .collapse-toggle {
  display: none;
}

.layout-wrapper--landscape-compact .sidebar-nav {
  padding: 8px 8px 6px;
}

.layout-wrapper--landscape-compact .nav-item {
  justify-content: center;
  padding: 0;
}

.layout-wrapper--landscape-compact .nav-divider {
  margin: 4px 8px;
}

.layout-wrapper--landscape-compact .sidebar-bottom {
  padding: 8px;
}

.layout-wrapper--landscape-compact .layout-header {
  height: 52px;
  padding: 0 16px;
}

.layout-wrapper--landscape-compact .header-left {
  gap: 10px;
}

.layout-wrapper--landscape-compact .header-action {
  width: 36px;
  height: 36px;
}

.layout-wrapper--landscape-compact .header-user {
  padding: 4px 8px;
  margin-left: 4px;
}

.layout-wrapper--landscape-compact .header-avatar {
  width: 28px;
  height: 28px;
  font-size: 12px;
}

.layout-wrapper--landscape-compact .header-username {
  font-size: 13px;
}

.layout-wrapper--landscape-compact .layout-content {
  padding: 12px;
}

/* ---- Dropdown Styles ---- */
:deep(.el-dropdown-menu__item) {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  font-size: 14px;
}

/* ---- Transitions ---- */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* ---- Notification Center ---- */
.notif-badge {
  margin-right: 4px;
}

.notif-header-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0 16px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 8px;
}

.notif-unread-info {
  font-size: 13px;
  color: #666;
}

.notif-item {
  display: flex;
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid #f5f5f5;
  position: relative;
  transition: background 0.2s;
}

.notif-item:hover {
  background: #f9f9f9;
}

.notif-item--unread {
  background: #f0f7ff;
}

.notif-item--unread:hover {
  background: #e8f3ff;
}

.notif-item-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #4F46E5;
  flex-shrink: 0;
  margin-top: 5px;
  margin-right: 10px;
}

.notif-item-content {
  flex: 1;
  min-width: 0;
}

.notif-item-title {
  font-size: 14px;
  font-weight: 500;
  color: #1a202c;
  margin-bottom: 4px;
}

.notif-item-body {
  font-size: 12px;
  color: #666;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notif-item-time {
  font-size: 11px;
  color: #aaa;
}

@media (max-width: 768px) {
  .layout-wrapper {
    display: block;
  }

  .layout-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(88vw, 320px);
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    box-shadow: 0 24px 48px rgba(15, 23, 42, 0.22);
  }

  .layout-sidebar.is-mobile-open {
    transform: translateX(0);
  }

  .layout-main-area {
    width: 100%;
    min-height: 100vh;
    min-height: 100dvh;
  }

  .layout-header {
    height: 54px;
    padding: 0 10px 0 8px;
  }

  .header-left,
  .header-right {
    min-width: 0;
  }

  .header-right {
    gap: 0;
  }

  .header-action {
    width: 34px;
    height: 34px;
  }

  .header-right > .header-action:first-child {
    display: none;
  }

  .header-user {
    padding: 4px;
    margin-left: 4px;
  }

  .header-username,
  .header-arrow {
    display: none;
  }

  .layout-content {
    padding: var(--tg-content-padding, 10px);
  }

  .notif-header-actions {
    padding-top: 0;
  }
}
</style>
