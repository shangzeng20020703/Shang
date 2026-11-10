<template>
  <ion-page
    ><ion-content
      ><ManagerInbox v-if="visible" :get="get" :post="post" />
      <p class="links">
        <router-link to="/app/tabs/approvals?segment=approved"
          >审批记录</router-link
        >
        · <router-link to="/app/monthly">月度考勤</router-link>
      </p></ion-content
    ></ion-page
  >
</template>
<script setup lang="ts">
import { ref } from 'vue'
import {
  IonContent,
  IonPage,
  onIonViewWillEnter,
  onIonViewDidLeave,
} from '@ionic/vue'
import ManagerInbox from '../../../shared/ManagerInbox.vue'
import { get, post } from '@/utils/request'
const visible = ref(true)
onIonViewWillEnter(() => (visible.value = true))
onIonViewDidLeave(() => (visible.value = false))
</script>
<style scoped>
.links {
  text-align: center;
  padding-bottom: 16px;
}
</style>
