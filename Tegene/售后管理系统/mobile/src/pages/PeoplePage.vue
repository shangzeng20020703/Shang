<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page">
      <div class="mobile-shell">
        <section class="solid-card" style="padding: 16px 18px">
          <h2 class="page-section-title">团队花名册</h2>
          <ion-searchbar v-model="keyword" placeholder="搜索姓名 / 工号" @ionInput="loadPeople" />
        </section>

        <section class="section-block stack-list">
          <article v-for="item in people" :key="item.id" class="solid-card" style="padding: 16px 18px">
            <div class="sheet-row">
              <strong class="sheet-value" style="text-align: left">{{ item.name }}</strong>
              <span class="compact-chip">{{ item.employee_no }}</span>
            </div>
            <div class="sheet-row"><span class="sheet-label">部门</span><span class="sheet-value">{{ item.department_name || '-' }}</span></div>
            <div class="sheet-row"><span class="sheet-label">岗位</span><span class="sheet-value">{{ item.position || '-' }}</span></div>
            <div class="sheet-row"><span class="sheet-label">状态</span><span class="sheet-value">{{ formatStatus(item.status) }}</span></div>
          </article>
          <section class="solid-card" style="padding: 18px" v-if="!people.length">
            <span class="muted-text">当前范围暂无员工数据</span>
          </section>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { IonContent, IonPage, IonSearchbar, onIonViewWillEnter } from '@ionic/vue'
import { get } from '@/utils/request'
import { formatStatus } from '@/utils/format'

const keyword = ref('')
const people = ref<any[]>([])

async function loadPeople() {
  const result = await get<any>('/field/directory')
  people.value = (result.people || []).filter((item: any) => `${item.name} ${item.employee_no}`.includes(keyword.value)).map((item: any) => ({...item, status: item.is_active ? '在职' : '停用'}))
}

onIonViewWillEnter(loadPeople)
</script>
