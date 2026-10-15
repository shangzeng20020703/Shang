<template>
  <div class="event-monitor-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">事件监控</h1>
        <p class="page-desc">审批终态事件、消费者幂等、考勤 effect 与重算标记</p>
      </div>
      <div class="page-actions">
        <el-button @click="refreshCurrent">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="dispatchPending">
          <el-icon><Connection /></el-icon>
          分发待处理
        </el-button>
      </div>
    </header>

    <section class="metric-grid">
      <div class="metric-tile metric-tile--amber">
        <span>待处理事件</span>
        <strong>{{ pendingEventCount }}</strong>
      </div>
      <div class="metric-tile metric-tile--red">
        <span>失败事件</span>
        <strong>{{ failedEventCount }}</strong>
      </div>
      <div class="metric-tile metric-tile--green">
        <span>有效 effect</span>
        <strong>{{ activeEffectCount }}</strong>
      </div>
      <div class="metric-tile metric-tile--blue">
        <span>重算待办</span>
        <strong>{{ pendingRecalcCount }}</strong>
      </div>
    </section>

    <el-alert
      v-if="monitorUnavailableMessage"
      :title="monitorUnavailableMessage"
      type="warning"
      show-icon
      :closable="false"
      class="monitor-alert"
    />

    <section class="monitor-panel">
      <el-tabs v-model="activeTab" class="monitor-tabs" @tab-change="handleTabChange">
        <el-tab-pane label="Outbox 事件" name="outbox">
          <div class="filter-bar">
            <el-select v-model="outboxFilters.status" placeholder="状态" clearable>
              <el-option v-for="item in outboxStatusOptions" :key="item" :label="getOutboxStatusLabel(item)" :value="item" />
            </el-select>
            <el-input v-model="outboxFilters.event_type" placeholder="事件类型" clearable />
            <el-input v-model="outboxFilters.source_module" placeholder="来源模块" clearable />
            <el-input v-model="outboxFilters.aggregate_id" placeholder="聚合 ID" clearable />
            <el-input v-model="outboxFilters.keyword" placeholder="事件 ID / 幂等键 / 错误" clearable />
            <el-button type="primary" @click="searchOutbox">
              <el-icon><Search /></el-icon>
              查询
            </el-button>
            <el-button @click="resetOutbox">重置</el-button>
          </div>

          <el-table :data="outboxRows" v-loading="outboxLoading" class="monitor-table">
            <el-table-column prop="id" label="ID" width="82" />
            <el-table-column label="审批单" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ approvalInstanceLabel(row) }}</span>
                  <small>{{ approvalSummary(row) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="审批类型" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ approvalTypeLabel(row) }}</span>
                  <small>{{ approvalBusinessCode(row) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="申请人" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ approvalApplicantLabel(row) }}</span>
                  <small>{{ approvalApplicantHint(row) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="事件类型" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ eventTypeLabel(row.event_type) }}</span>
                  <small>{{ row.event_type }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="132">
              <template #default="{ row }">
                <el-tag :type="outboxStatusType(row.status)" effect="light" round>{{ getOutboxStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="业务说明" min-width="320" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ outboxMeaning(row) }}</span>
                  <small>{{ outboxStatusHint(row.status) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="attempt_count" label="尝试" width="78" align="center" />
            <el-table-column prop="occurred_at" label="发生时间" width="172">
              <template #default="{ row }">{{ formatDateTime(row.occurred_at) }}</template>
            </el-table-column>
            <el-table-column prop="last_error" label="最后错误" min-width="220" show-overflow-tooltip />
            <el-table-column label="操作" width="210" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="showConsumptionsFor(row)">消费记录</el-button>
                <el-button link type="primary" size="small" @click="openDetail('Outbox 事件详情', row)">详情</el-button>
                <el-button v-if="canDispatch(row)" link type="warning" size="small" @click="dispatchOne(row)">分发</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="outboxPage.page"
              v-model:page-size="outboxPage.page_size"
              :total="outboxPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              background
              @size-change="fetchOutbox"
              @current-change="fetchOutbox"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="消费记录" name="consumptions">
          <div class="filter-bar">
            <el-select v-model="consumptionFilters.status" placeholder="状态" clearable>
              <el-option v-for="item in consumptionStatusOptions" :key="item" :label="consumptionStatusLabel(item)" :value="item" />
            </el-select>
            <el-input v-model="consumptionFilters.consumer_name" placeholder="消费者" clearable />
            <el-input v-model="consumptionFilters.event_id" placeholder="事件 ID" clearable />
            <el-button type="primary" @click="searchConsumptions">
              <el-icon><Search /></el-icon>
              查询
            </el-button>
            <el-button @click="resetConsumptions">重置</el-button>
          </div>

          <el-table :data="consumptionRows" v-loading="consumptionLoading" class="monitor-table">
            <el-table-column prop="id" label="ID" width="82" />
            <el-table-column label="消费者" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ consumerNameLabel(row.consumer_name) }}</span>
                  <small>{{ row.consumer_name }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="132">
              <template #default="{ row }">
                <el-tag :type="consumptionStatusType(row.status)" effect="light" round>{{ consumptionStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="事件类型" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ eventTypeLabel(row.event_type) }}</span>
                  <small>{{ row.event_type || '-' }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="event_status" label="事件状态" width="132">
              <template #default="{ row }">
                <el-tag :type="outboxStatusType(row.event_status)" effect="light" round>{{ getOutboxStatusLabel(row.event_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="处理结论" min-width="300" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="semantic-cell">
                  <span>{{ consumptionConclusion(row) }}</span>
                  <small>{{ consumptionStatusHint(row.status) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="attempt_count" label="尝试" width="78" align="center" />
            <el-table-column prop="processed_at" label="处理时间" width="172">
              <template #default="{ row }">{{ formatDateTime(row.processed_at) }}</template>
            </el-table-column>
            <el-table-column prop="last_error" label="最后错误" min-width="220" show-overflow-tooltip />
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail('消费记录详情', row)">详情</el-button>
                <el-button v-if="canReprocessConsumption(row)" link type="warning" size="small" @click="reprocessConsumption(row)">重新消费</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="consumptionPage.page"
              v-model:page-size="consumptionPage.page_size"
              :total="consumptionPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              background
              @size-change="fetchConsumptions"
              @current-change="fetchConsumptions"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="考勤影响" name="effects">
          <div class="filter-bar">
            <el-select v-model="effectFilters.effect_type" placeholder="effect 类型" clearable>
              <el-option v-for="item in effectTypeOptions" :key="item" :label="effectTypeLabel(item)" :value="item" />
            </el-select>
            <el-select v-model="effectFilters.effect_status" placeholder="状态" clearable>
              <el-option v-for="item in effectStatusOptions" :key="item" :label="effectStatusLabel(item)" :value="item" />
            </el-select>
            <el-input v-model="effectFilters.employee_id" placeholder="员工 ID" clearable />
            <el-input v-model="effectFilters.source_business_type" placeholder="业务类型" clearable />
            <el-date-picker
              v-model="effectDateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
            />
            <el-button type="primary" @click="searchEffects">
              <el-icon><Search /></el-icon>
              查询
            </el-button>
            <el-button @click="resetEffects">重置</el-button>
          </div>

          <el-table :data="effectRows" v-loading="effectLoading" class="monitor-table">
            <el-table-column prop="id" label="ID" width="82" />
            <el-table-column label="员工" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">{{ employeeLabel(row) }}</template>
            </el-table-column>
            <el-table-column label="类型" width="132">
              <template #default="{ row }">{{ effectTypeLabel(row.effect_type) }}</template>
            </el-table-column>
            <el-table-column prop="effect_status" label="状态" width="132">
              <template #default="{ row }">
                <el-tag :type="effectStatusType(row.effect_status)" effect="light" round>{{ effectStatusLabel(row.effect_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="work_date" label="考勤日期" width="122" />
            <el-table-column prop="minutes" label="分钟" width="82" align="right" />
            <el-table-column prop="pay_policy" label="薪酬口径" width="120" />
            <el-table-column prop="attendance_status" label="考勤状态" width="112" />
            <el-table-column label="来源业务" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.source_business_type || '-' }} #{{ row.source_business_id || '-' }}</template>
            </el-table-column>
            <el-table-column prop="source_event_id" label="事件 ID" min-width="230" show-overflow-tooltip />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail('Attendance Effect 详情', row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="effectPage.page"
              v-model:page-size="effectPage.page_size"
              :total="effectPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              background
              @size-change="fetchEffects"
              @current-change="fetchEffects"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="重算标记" name="recalc">
          <div class="filter-bar">
            <el-select v-model="recalcFilters.status" placeholder="状态" clearable>
              <el-option v-for="item in recalcStatusOptions" :key="item" :label="recalcStatusLabel(item)" :value="item" />
            </el-select>
            <el-input v-model="recalcFilters.employee_id" placeholder="员工 ID" clearable />
            <el-input v-model="recalcFilters.source_event_id" placeholder="事件 ID" clearable />
            <el-date-picker
              v-model="recalcDateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
            />
            <el-button type="primary" @click="searchRecalc">
              <el-icon><Search /></el-icon>
              查询
            </el-button>
            <el-button @click="resetRecalc">重置</el-button>
          </div>

          <el-table :data="recalcRows" v-loading="recalcLoading" class="monitor-table">
            <el-table-column prop="id" label="ID" width="82" />
            <el-table-column label="员工" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">{{ employeeLabel(row) }}</template>
            </el-table-column>
            <el-table-column prop="work_date" label="考勤日期" width="122" />
            <el-table-column prop="reason" label="原因" width="140" />
            <el-table-column prop="status" label="状态" width="112">
              <template #default="{ row }">
                <el-tag :type="recalcStatusType(row.status)" effect="light" round>{{ recalcStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="source_event_id" label="事件 ID" min-width="230" show-overflow-tooltip />
            <el-table-column prop="last_error" label="最后错误" min-width="220" show-overflow-tooltip />
            <el-table-column prop="created_at" label="创建时间" width="172">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail('重算标记详情', row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="recalcPage.page"
              v-model:page-size="recalcPage.page_size"
              :total="recalcPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              background
              @size-change="fetchRecalc"
              @current-change="fetchRecalc"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-drawer v-model="detailVisible" :title="detailTitle" direction="rtl" size="560px">
      <pre class="json-view">{{ detailJson }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { isAxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Refresh, Search } from '@element-plus/icons-vue'
import { get, post } from '@/utils/request'

type TabName = 'outbox' | 'consumptions' | 'effects' | 'recalc'

const activeTab = ref<TabName>('outbox')
const summary = ref<any>({})

const outboxRows = ref<any[]>([])
const consumptionRows = ref<any[]>([])
const effectRows = ref<any[]>([])
const recalcRows = ref<any[]>([])
const applicantNameCache = reactive<Record<number, { name: string; employee_no?: string }>>({})

const outboxLoading = ref(false)
const consumptionLoading = ref(false)
const effectLoading = ref(false)
const recalcLoading = ref(false)

const detailVisible = ref(false)
const detailTitle = ref('')
const detailPayload = ref<any>({})

const outboxStatusOptions = ['pending', 'dispatching', 'dispatched', 'failed', 'dead']
const consumptionStatusOptions = ['processing', 'succeeded', 'failed', 'skipped']
const effectTypeOptions = ['leave', 'overtime', 'business_trip', 'outside', 'punch_correction']
const effectStatusOptions = ['active', 'revoked', 'superseded']
const recalcStatusOptions = ['pending', 'processing', 'succeeded', 'failed']

const outboxStatusText: Record<string, string> = {
  pending: '待分发',
  dispatching: '分发中',
  dispatched: '已分发',
  failed: '分发失败',
  dead: '停止重试',
}

const consumptionStatusText: Record<string, string> = {
  processing: '处理中',
  succeeded: '已处理',
  failed: '处理失败',
  skipped: '已跳过',
}

const effectTypeText: Record<string, string> = {
  leave: '请假',
  overtime: '加班',
  business_trip: '出差',
  outside: '外出',
  punch_correction: '补卡',
}

const effectStatusText: Record<string, string> = {
  active: '生效中',
  revoked: '已撤销',
  superseded: '已被替代',
}

const recalcStatusText: Record<string, string> = {
  pending: '待重算',
  processing: '重算中',
  succeeded: '已重算',
  failed: '重算失败',
}

const eventTypeText: Record<string, string> = {
  'approval.instance.approved.v1': '审批已通过',
  'approval.instance.rejected.v1': '审批已拒绝',
  'approval.instance.revoked.v1': '审批已撤回',
  'approval.instance.cancelled.v1': '审批已取消',
}

const approvalBusinessTypeText: Record<string, string> = {
  attendance_outside: '外出',
  outside: '外出',
  punch_correction: '补卡',
  attendance_punch_correction: '补卡',
  patch_apply: '补卡',
  leave: '请假',
  leave_request: '请假',
  on_leave: '请假',
  business_trip: '出差',
  overtime: '加班',
}

const consumerNameText: Record<string, string> = {
  'attendance.approval_effect_consumer': '考勤审批影响消费者',
}

const outboxFilters = reactive({
  status: '',
  event_type: '',
  source_module: '',
  aggregate_type: '',
  aggregate_id: '',
  keyword: '',
})
const consumptionFilters = reactive({
  status: '',
  consumer_name: '',
  event_id: '',
})
const effectFilters = reactive({
  effect_type: '',
  effect_status: '',
  employee_id: '',
  source_business_type: '',
})
const recalcFilters = reactive({
  status: '',
  employee_id: '',
  source_event_id: '',
})

const effectDateRange = ref<string[]>([])
const recalcDateRange = ref<string[]>([])

const outboxPage = reactive({ page: 1, page_size: 20, total: 0 })
const consumptionPage = reactive({ page: 1, page_size: 20, total: 0 })
const effectPage = reactive({ page: 1, page_size: 20, total: 0 })
const recalcPage = reactive({ page: 1, page_size: 20, total: 0 })

const pendingEventCount = computed(() => statusCount('outbox_by_status', 'pending'))
const failedEventCount = computed(() => statusCount('outbox_by_status', 'failed') + statusCount('outbox_by_status', 'dead'))
const activeEffectCount = computed(() => statusCount('effect_by_status', 'active'))
const pendingRecalcCount = computed(() => statusCount('recalc_by_status', 'pending'))
const detailJson = computed(() => JSON.stringify(detailPayload.value || {}, null, 2))
const monitorUnavailableMessage = ref('')
const monitorUnavailableNotified = ref(false)

function statusCount(group: string, key: string) {
  return Number(summary.value?.[group]?.[key] || 0)
}

function prune(params: Record<string, any>) {
  const cleaned: Record<string, any> = {}
  Object.entries(params).forEach(([key, value]) => {
    if (value === '' || value === null || value === undefined) return
    cleaned[key] = value
  })
  return cleaned
}

function numericOrUndefined(value: string) {
  const trimmed = String(value || '').trim()
  if (!trimmed) return undefined
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

function formatDateTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function employeeLabel(row: any) {
  const name = row.employee_name || `员工 #${row.employee_id || '-'}`
  return row.employee_no ? `${name}（${row.employee_no}）` : name
}

function fallbackLabel(map: Record<string, string>, value?: string | null) {
  const key = String(value || '')
  if (!key) return '-'
  return map[key] || key
}

function getOutboxStatusLabel(status?: string | null) {
  return fallbackLabel(outboxStatusText, status)
}

function consumptionStatusLabel(status?: string | null) {
  return fallbackLabel(consumptionStatusText, status)
}

function effectTypeLabel(type?: string | null) {
  return fallbackLabel(effectTypeText, type)
}

function effectStatusLabel(status?: string | null) {
  return fallbackLabel(effectStatusText, status)
}

function recalcStatusLabel(status?: string | null) {
  return fallbackLabel(recalcStatusText, status)
}

function eventTypeLabel(type?: string | null) {
  return fallbackLabel(eventTypeText, type)
}

function consumerNameLabel(name?: string | null) {
  return fallbackLabel(consumerNameText, name)
}

function eventPayload(row: any) {
  const payload = row?.payload_json
  return payload && typeof payload === 'object' ? payload : {}
}

function approvalContext(row: any) {
  const payload = eventPayload(row)
  const context = row?.approval_context && typeof row.approval_context === 'object'
    ? row.approval_context
    : {}
  const applicantId = Number(context.applicant_id || payload.applicant_id || 0)
  const cachedApplicant = applicantId ? applicantNameCache[applicantId] : undefined
  const merged: Record<string, any> = {
    approval_instance_id: payload.approval_instance_id || (row?.aggregate_type === 'approval_instance' ? row?.aggregate_id : undefined),
    module: payload.module,
    business_type: payload.business_type,
    business_id: payload.business_id,
    summary: payload.summary,
    applicant_id: payload.applicant_id,
    applicant_name: cachedApplicant?.name || payload.applicant_name,
    applicant_no: cachedApplicant?.employee_no || payload.applicant_no,
    approval_type_name: payload.approval_type_name,
    business_type_label: payload.business_type_label,
  }
  Object.entries(context).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') return
    merged[key] = value
  })
  return merged
}

function businessTypeFallbackLabel(...codes: Array<string | null | undefined>) {
  for (const rawCode of codes) {
    const code = String(rawCode || '').trim().toLowerCase()
    if (!code) continue
    if (approvalBusinessTypeText[code]) return approvalBusinessTypeText[code]
    if (code.startsWith('attendance_rule_')) {
      if (code.endsWith('_patch_apply')) return '补卡'
      if (code.endsWith('_leave_punch')) return '请假打卡'
      if (code.endsWith('_overtime')) return '加班'
      if (code.endsWith('_approve_punch')) return '审批打卡'
    }
    if (code.includes('outside')) return '外出'
    if (code.includes('punch') || code.includes('patch')) return '补卡'
    if (code.includes('leave')) return '请假'
    if (code.includes('business_trip') || code.includes('trip')) return '出差'
    if (code.includes('overtime')) return '加班'
  }
  return ''
}

function approvalInstanceLabel(row: any) {
  const context = approvalContext(row)
  const instanceId = context.approval_instance_id || row?.aggregate_id
  if (instanceId) return `审批单 #${instanceId}`
  return aggregateLabel(row)
}

function approvalSummary(row: any) {
  const context = approvalContext(row)
  return context.summary || context.approval_type_name || context.business_type_label || '-'
}

function approvalTypeLabel(row: any) {
  const context = approvalContext(row)
  return (
    context.approval_type_name
    || context.business_type_label
    || businessTypeFallbackLabel(context.business_type, context.module)
    || '-'
  )
}

function approvalBusinessCode(row: any) {
  const context = approvalContext(row)
  return context.business_type || context.module || '-'
}

function approvalApplicantLabel(row: any) {
  const context = approvalContext(row)
  if (context.applicant_name) return context.applicant_name
  return '-'
}

function approvalApplicantHint(row: any) {
  const context = approvalContext(row)
  if (context.applicant_name && context.applicant_no) return context.applicant_no
  return '申请人'
}

function aggregateLabel(row: any) {
  const aggregateType = String(row?.aggregate_type || '')
  const aggregateId = row?.aggregate_id || '-'
  if (aggregateType === 'approval_instance') return `审批单 #${aggregateId}`
  return `${aggregateType || '聚合对象'} #${aggregateId}`
}

function outboxStatusHint(status?: string | null) {
  const text: Record<string, string> = {
    pending: '事件已写入，等待分发给消费者。',
    dispatching: '系统正在把事件交给订阅消费者。',
    dispatched: '事件已完成分发；是否产生业务结果看消费记录。',
    failed: '分发失败，可查看最后错误后重试。',
    dead: '多次失败后停止自动重试，需要人工处理。',
  }
  return text[String(status || '')] || '未知状态，请查看详情。'
}

function consumptionStatusHint(status?: string | null) {
  const text: Record<string, string> = {
    processing: '消费者正在处理这条事件。',
    succeeded: '消费者已执行业务副作用。',
    failed: '消费者处理时报错，业务副作用未可靠完成。',
    skipped: '消费者看过事件，但判定本次没有可落地的业务结果。',
  }
  return text[String(status || '')] || '未知状态，请查看详情。'
}

function outboxMeaning(row: any) {
  const type = String(row?.event_type || '')
  const subject = [
    approvalInstanceLabel(row),
    approvalTypeLabel(row) !== '-' ? approvalTypeLabel(row) : '',
    approvalApplicantLabel(row) !== '-' && approvalApplicantLabel(row) !== '姓名未返回' ? approvalApplicantLabel(row) : '',
  ].filter(Boolean).join(' · ')
  if (type === 'approval.instance.approved.v1') return `${subject} 审批通过，通知下游业务模块处理。`
  if (type === 'approval.instance.rejected.v1') return `${subject} 审批拒绝，通知下游业务模块回滚或关闭。`
  if (type === 'approval.instance.revoked.v1') return `${subject} 已撤回，通知下游撤销影响。`
  if (type === 'approval.instance.cancelled.v1') return `${subject} 已取消，通知下游撤销影响。`
  return `${subject || aggregateLabel(row)} 产生了业务事件。`
}

function consumptionConclusion(row: any) {
  const status = String(row?.status || '')
  const consumer = consumerNameLabel(row?.consumer_name)
  if (status === 'succeeded') return `${consumer} 已处理，业务影响已落库。`
  if (status === 'skipped') return `${consumer} 已跳过，本次没有生成考勤影响或重算任务。`
  if (status === 'failed') return `${consumer} 处理失败，需要查看错误并重新消费。`
  if (status === 'processing') return `${consumer} 正在处理中。`
  return `${consumer} 状态未知。`
}

function outboxStatusType(status?: string) {
  if (status === 'dispatched') return 'success'
  if (status === 'failed' || status === 'dead') return 'danger'
  if (status === 'dispatching') return 'warning'
  return 'info'
}

function consumptionStatusType(status?: string) {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'skipped') return 'info'
  return 'warning'
}

function effectStatusType(status?: string) {
  if (status === 'active') return 'success'
  if (status === 'revoked') return 'info'
  return 'warning'
}

function recalcStatusType(status?: string) {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'pending') return 'warning'
  return 'info'
}

function canDispatch(row: any) {
  return ['pending', 'failed', 'dead'].includes(String(row?.status || ''))
}

function canReprocessConsumption(row: any) {
  return ['failed', 'skipped'].includes(String(row?.status || ''))
}

function isDialogCancel(error: any) {
  return error === 'cancel' || error === 'close' || error?.action === 'cancel' || error?.action === 'close'
}

function isAuthRequestError(error: unknown) {
  return isAxiosError(error) && [401, 403].includes(error.response?.status ?? 0)
}

function rethrowNonAuthRequestError(error: unknown) {
  if (isAuthRequestError(error)) return
  throw error
}

function monitorRequestConfig() {
  return {
    skipDefaultErrorHandler: true,
    skipErrorReport: true,
    skipNetworkErrorReport: true,
  }
}

function resetMonitorData() {
  summary.value = {}
  outboxRows.value = []
  consumptionRows.value = []
  effectRows.value = []
  recalcRows.value = []
  outboxPage.total = 0
  consumptionPage.total = 0
  effectPage.total = 0
  recalcPage.total = 0
}

function markMonitorAvailable() {
  monitorUnavailableMessage.value = ''
  monitorUnavailableNotified.value = false
}

function markMonitorUnavailable() {
  monitorUnavailableMessage.value = '事件监控接口暂不可用，请确认后端已同步业务事件与考勤影响相关表结构后重试。'
  resetMonitorData()
  if (!monitorUnavailableNotified.value) {
    ElMessage.warning(monitorUnavailableMessage.value)
    monitorUnavailableNotified.value = true
  }
}

function handleMonitorRequestError(error: unknown) {
  if (isAuthRequestError(error)) {
    if (isAxiosError(error) && error.response?.status === 403) {
      ElMessage.error('没有权限访问事件监控')
    }
    return
  }

  if (isAxiosError(error)) {
    const status = error.response?.status ?? 0
    if (!error.response || status === 404 || status === 500) {
      markMonitorUnavailable()
      return
    }
  }

  throw error
}

function openDetail(title: string, payload: any) {
  detailTitle.value = title
  detailPayload.value = payload
  detailVisible.value = true
}

async function hydrateOutboxApplicantNames(rows: any[]) {
  const ids = Array.from(new Set(
    rows
      .map((row) => Number(approvalContext(row).applicant_id || 0))
      .filter((id) => id > 0 && !applicantNameCache[id]),
  ))
  if (!ids.length) return
  await Promise.all(ids.map(async (id) => {
    try {
      const employee: any = await get(`/employees/${id}`, undefined, {
        skipDefaultErrorHandler: true,
        skipErrorReport: true,
      })
      if (employee?.name) {
        applicantNameCache[id] = {
          name: String(employee.name),
          employee_no: employee.employee_no ? String(employee.employee_no) : undefined,
        }
      }
    } catch (_error) {
      // 员工姓名兜底查询失败时保持空白，不在监控页暴露内部员工 ID。
    }
  }))
}

async function fetchSummary() {
  try {
    summary.value = await get('/event-monitor/summary', undefined, monitorRequestConfig())
    markMonitorAvailable()
  } catch (error) {
    handleMonitorRequestError(error)
  }
}

async function fetchOutbox() {
  outboxLoading.value = true
  try {
    const res: any = await get(
      '/event-monitor/outbox',
      prune({
        ...outboxFilters,
        aggregate_id: numericOrUndefined(outboxFilters.aggregate_id),
        page: outboxPage.page,
        page_size: outboxPage.page_size,
      }),
      monitorRequestConfig(),
    )
    const items = res.items || []
    outboxRows.value = items
    await hydrateOutboxApplicantNames(items)
    outboxPage.total = Number(res.total || 0)
  } catch (error) {
    handleMonitorRequestError(error)
  } finally {
    outboxLoading.value = false
  }
}

async function fetchConsumptions() {
  consumptionLoading.value = true
  try {
    const res: any = await get(
      '/event-monitor/consumptions',
      prune({
        ...consumptionFilters,
        page: consumptionPage.page,
        page_size: consumptionPage.page_size,
      }),
      monitorRequestConfig(),
    )
    consumptionRows.value = res.items || []
    consumptionPage.total = Number(res.total || 0)
  } catch (error) {
    handleMonitorRequestError(error)
  } finally {
    consumptionLoading.value = false
  }
}

async function fetchEffects() {
  effectLoading.value = true
  try {
    const [from, to] = effectDateRange.value || []
    const res: any = await get(
      '/event-monitor/effects',
      prune({
        ...effectFilters,
        employee_id: numericOrUndefined(effectFilters.employee_id),
        work_date_from: from,
        work_date_to: to,
        page: effectPage.page,
        page_size: effectPage.page_size,
      }),
      monitorRequestConfig(),
    )
    effectRows.value = res.items || []
    effectPage.total = Number(res.total || 0)
  } catch (error) {
    handleMonitorRequestError(error)
  } finally {
    effectLoading.value = false
  }
}

async function fetchRecalc() {
  recalcLoading.value = true
  try {
    const [from, to] = recalcDateRange.value || []
    const res: any = await get(
      '/event-monitor/recalc-tasks',
      prune({
        ...recalcFilters,
        employee_id: numericOrUndefined(recalcFilters.employee_id),
        work_date_from: from,
        work_date_to: to,
        page: recalcPage.page,
        page_size: recalcPage.page_size,
      }),
      monitorRequestConfig(),
    )
    recalcRows.value = res.items || []
    recalcPage.total = Number(res.total || 0)
  } catch (error) {
    handleMonitorRequestError(error)
  } finally {
    recalcLoading.value = false
  }
}

function searchOutbox() {
  outboxPage.page = 1
  fetchOutbox()
}

function searchConsumptions() {
  consumptionPage.page = 1
  fetchConsumptions()
}

function searchEffects() {
  effectPage.page = 1
  fetchEffects()
}

function searchRecalc() {
  recalcPage.page = 1
  fetchRecalc()
}

function resetOutbox() {
  Object.assign(outboxFilters, { status: '', event_type: '', source_module: '', aggregate_type: '', aggregate_id: '', keyword: '' })
  searchOutbox()
}

function resetConsumptions() {
  Object.assign(consumptionFilters, { status: '', consumer_name: '', event_id: '' })
  searchConsumptions()
}

function resetEffects() {
  Object.assign(effectFilters, { effect_type: '', effect_status: '', employee_id: '', source_business_type: '' })
  effectDateRange.value = []
  searchEffects()
}

function resetRecalc() {
  Object.assign(recalcFilters, { status: '', employee_id: '', source_event_id: '' })
  recalcDateRange.value = []
  searchRecalc()
}

async function refreshCurrent() {
  await fetchSummary()
  if (monitorUnavailableMessage.value) return
  if (activeTab.value === 'outbox') await fetchOutbox()
  if (activeTab.value === 'consumptions') await fetchConsumptions()
  if (activeTab.value === 'effects') await fetchEffects()
  if (activeTab.value === 'recalc') await fetchRecalc()
}

async function handleTabChange() {
  await refreshCurrent()
}

async function showConsumptionsFor(row: any) {
  activeTab.value = 'consumptions'
  consumptionFilters.event_id = row.event_id || ''
  consumptionPage.page = 1
  await fetchConsumptions()
}

async function dispatchOne(row: any) {
  try {
    await ElMessageBox.confirm(`确认分发事件 #${row.id}？`, '分发确认', { type: 'warning' })
    const updated = await post(`/event-monitor/outbox/${row.id}/dispatch`, {})
    ElMessage.success('分发完成')
    openDetail('Outbox 分发结果', updated)
    await Promise.all([fetchSummary(), fetchOutbox(), fetchConsumptions(), fetchEffects(), fetchRecalc()])
  } catch (error: any) {
    if (isDialogCancel(error) || isAuthRequestError(error)) return
    throw error
  }
}

async function dispatchPending() {
  try {
    const res: any = await post('/event-monitor/outbox/dispatch-pending?limit=50', {})
    ElMessage.success(`已处理 ${res.count || 0} 条事件`)
    await Promise.all([fetchSummary(), fetchOutbox(), fetchConsumptions(), fetchEffects(), fetchRecalc()])
  } catch (error) {
    rethrowNonAuthRequestError(error)
  }
}

async function reprocessConsumption(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认重新消费记录 #${row.id}？这会再次执行对应消费者的业务处理。`,
      '重新消费确认',
      { type: 'warning' },
    )
    const updated = await post(`/event-monitor/consumptions/${row.id}/reprocess`, {})
    ElMessage.success('重新消费完成')
    openDetail('重新消费结果', updated)
    await Promise.all([fetchSummary(), fetchOutbox(), fetchConsumptions(), fetchEffects(), fetchRecalc()])
  } catch (error: any) {
    if (isDialogCancel(error) || isAuthRequestError(error)) return
    throw error
  }
}

onMounted(async () => {
  await fetchSummary()
  if (monitorUnavailableMessage.value) return
  await fetchOutbox()
})
</script>

<style scoped>
.event-monitor-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.monitor-alert {
  margin-top: -4px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.page-title {
  margin: 0;
  color: #0f172a;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0;
}

.page-desc {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 14px;
}

.page-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-tile {
  min-height: 92px;
  padding: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  display: grid;
  align-content: center;
  gap: 8px;
}

.metric-tile span {
  color: #64748b;
  font-size: 13px;
}

.metric-tile strong {
  color: #0f172a;
  font-size: 28px;
  line-height: 1;
  letter-spacing: 0;
}

.metric-tile--amber {
  border-left: 4px solid #f59e0b;
}

.metric-tile--red {
  border-left: 4px solid #ef4444;
}

.metric-tile--green {
  border-left: 4px solid #10b981;
}

.metric-tile--blue {
  border-left: 4px solid #2563eb;
}

.monitor-panel {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  padding: 14px 16px 16px;
}

.filter-bar {
  display: grid;
  grid-template-columns: repeat(5, minmax(150px, 1fr)) auto auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
}

.monitor-table {
  width: 100%;
}

.semantic-cell {
  min-width: 0;
  display: grid;
  gap: 3px;
  line-height: 1.35;
}

.semantic-cell span {
  color: #0f172a;
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.semantic-cell small {
  color: #64748b;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  padding-top: 14px;
}

.json-view {
  margin: 0;
  padding: 14px;
  min-height: 320px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.6;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 1200px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-bar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .page-header {
    flex-direction: column;
  }

  .metric-grid,
  .filter-bar {
    grid-template-columns: 1fr;
  }
}
</style>
