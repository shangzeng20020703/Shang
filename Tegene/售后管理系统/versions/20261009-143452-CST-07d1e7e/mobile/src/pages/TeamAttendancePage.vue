<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page">
      <div class="mobile-shell">
        <section class="solid-card" style="padding: 16px 18px">
          <div class="section-head">
            <h2 class="page-section-title" style="margin: 0">团队考勤</h2>
            <span class="compact-chip">{{ `${year}-${String(month).padStart(2, '0')}` }}</span>
          </div>
          <p class="muted-text" style="margin: 10px 0 0">聚合今日出勤、待审批请假和团队异常，方便项目经理和售后管理员 快速判断当天风险。</p>
        </section>

        <section class="section-block mini-grid">
          <div class="mini-card">
            <div class="mini-card__label">团队人数</div>
            <div class="mini-card__value">{{ rows.length }}</div>
          </div>
          <div class="mini-card">
            <div class="mini-card__label">待审请假</div>
            <div class="mini-card__value">{{ pendingLeaveCount }}</div>
          </div>
          <div class="mini-card">
            <div class="mini-card__label">今日迟到</div>
            <div class="mini-card__value">{{ todayStats.late_count || 0 }}</div>
          </div>
          <div class="mini-card">
            <div class="mini-card__label">今日未打卡</div>
            <div class="mini-card__value">{{ todayStats.absent_count || 0 }}</div>
          </div>
        </section>

        <section class="section-block solid-card" style="padding: 16px 18px">
          <h2 class="page-section-title">今日团队提醒</h2>
          <div class="summary-grid" style="margin-top: 12px">
            <div class="summary-card">
              <div class="summary-card__label">应出勤人数</div>
              <div class="summary-card__value">{{ todayStats.total_scheduled || 0 }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-card__label">已打卡人数</div>
              <div class="summary-card__value">{{ todayStats.clocked_in || 0 }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-card__label">月度总异常</div>
              <div class="summary-card__value">{{ totalAnomaly }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-card__label">月度总加班</div>
              <div class="summary-card__value">{{ totalOvertime.toFixed(1) }}</div>
            </div>
          </div>
        </section>

        <section v-if="anomalyLeaders.length" class="section-block solid-card" style="padding: 16px 18px">
          <h2 class="page-section-title">异常关注人员</h2>
          <div class="rank-list" style="margin-top: 12px">
            <div v-for="(item, index) in anomalyLeaders" :key="item.id" class="rank-item">
              <div class="rank-item__index">{{ index + 1 }}</div>
              <div class="rank-item__main">
                <div class="rank-item__title">{{ item.name }}</div>
                <div class="rank-item__meta">
                  {{ item.department_name || '-' }} / {{ item.position || '-' }} ·
                  异常 {{ item.summary?.anomaly_count || 0 }} 次，迟到 {{ item.summary?.late_count || 0 }} 次
                </div>
              </div>
              <span class="compact-chip">{{ overtimeHours(item.summary).toFixed(1) }}h</span>
            </div>
          </div>
        </section>

        <section class="section-block stack-list">
          <article v-for="item in rows" :key="item.id" class="solid-card" style="padding: 16px 18px">
            <div class="sheet-row">
              <strong class="sheet-value" style="text-align: left">{{ item.name }}</strong>
              <span class="compact-chip">{{ item.employee_no }}</span>
            </div>
            <div class="sheet-row"><span class="sheet-label">部门 / 岗位</span><span class="sheet-value">{{ item.department_name || '-' }} / {{ item.position || '-' }}</span></div>
            <div class="sheet-row"><span class="sheet-label">迟到 / 早退</span><span class="sheet-value">{{ item.summary?.late_count || 0 }} / {{ item.summary?.early_leave_count || 0 }}</span></div>
            <div class="sheet-row"><span class="sheet-label">异常次数</span><span class="sheet-value">{{ item.summary?.anomaly_count || 0 }}</span></div>
            <div class="sheet-row"><span class="sheet-label">加班小时</span><span class="sheet-value">{{ overtimeHours(item.summary).toFixed(1) }}</span></div>
            <div class="sheet-row"><span class="sheet-label">汇总状态</span><span class="sheet-value">{{ formatStatus(item.summary?.status) }}</span></div>
          </article>
        </section>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { IonContent, IonPage, onIonViewWillEnter } from '@ionic/vue'
import { get } from '@/utils/request'
import { chinaNowYearMonth, formatStatus } from '@/utils/format'

const { year, month } = chinaNowYearMonth()

const rows = ref<any[]>([])
const pendingLeaveCount = ref(0)
const todayStats = reactive<Record<string, any>>({})

function overtimeHours(summary: any) {
  if (!summary) return 0
  return Number(summary.overtime_weekday_hours || 0)
    + Number(summary.overtime_weekend_hours || 0)
    + Number(summary.overtime_holiday_hours || 0)
}

const totalAnomaly = computed(() => rows.value.reduce((sum, item) => sum + Number(item.summary?.anomaly_count || 0), 0))
const totalOvertime = computed(() => rows.value.reduce((sum, item) => sum + overtimeHours(item.summary), 0))
const anomalyLeaders = computed(() => {
  return [...rows.value]
    .sort((a, b) => Number(b.summary?.anomaly_count || 0) - Number(a.summary?.anomaly_count || 0))
    .filter((item) => Number(item.summary?.anomaly_count || 0) > 0)
    .slice(0, 3)
})

async function loadTeamAttendance() {
  const result = await get<any>('/field/team-attendance', { year, month })
  rows.value = result.people
  Object.assign(todayStats, result.today)
  pendingLeaveCount.value = result.pending_leave_count
}

onIonViewWillEnter(loadTeamAttendance)
</script>
