<template>
  <template v-if="isManager">
    <nav class="approval-nav">
      <router-link to="/approval">待我处理</router-link
      ><router-link to="/approval?view=records">审批记录与模板</router-link>
    </nav>
    <ManagerInbox
      v-if="route.query.view !== 'records'"
      :get="get"
      :post="post"
    />
    <ApprovalView v-else />
  </template>
  <ApprovalView v-else />
</template>
<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useRoute } from 'vue-router'
import { useUserAccess } from '@/composables/useUserAccess'
import { get, post } from '@/utils/request'
import ManagerInbox from '../../../shared/ManagerInbox.vue'
const route = useRoute(),
  { roles } = useUserAccess()
const isManager = computed(() => roles.value.includes('aftersales_manager'))
const ApprovalView = defineAsyncComponent(() => import('./ApprovalView.vue'))
</script>
<style scoped>
.approval-nav {
  display: flex;
  gap: 18px;
  margin: 0 0 14px;
}
</style>
