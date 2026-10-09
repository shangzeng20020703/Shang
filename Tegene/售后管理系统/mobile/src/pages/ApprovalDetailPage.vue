<template>
  <ion-page>
    <ion-content fullscreen class="wecom-detail-content">
      <div class="wecom-approval-detail-page">
        <header class="wecom-detail-nav">
          <button class="wecom-nav-button" type="button" aria-label="返回" @click="router.back()">
            <ion-icon :icon="chevronBackOutline" />
          </button>
          <h1>{{ approvalDetailPageTitle }}</h1>
          <div class="wecom-detail-nav__actions">
            <button class="wecom-nav-button wecom-nav-button--pdf" type="button" aria-label="导出PDF" @click="exportApprovalPdf">
              <span>PDF</span>
              <ion-icon :icon="downloadOutline" />
            </button>
            <button class="wecom-nav-button" type="button" aria-label="分享" @click="shareApprovalDetail">
              <ion-icon :icon="shareOutline" />
            </button>
          </div>
        </header>

        <main v-if="detail" class="wecom-detail-main">
          <section class="wecom-applicant-section" aria-label="申请人">
            <div class="wecom-avatar" aria-hidden="true">{{ applicantAvatarLetter }}</div>
            <div class="wecom-applicant-copy">
              <div class="wecom-applicant-title">
                <span>{{ applicantText }}的{{ approvalPlainName }}</span>
                <span class="wecom-status-badge" :class="`wecom-status-badge--${statusTone}`">{{ statusBadgeText }}</span>
              </div>
              <div class="wecom-applicant-meta">
                <span v-for="item in applicantMetaItems" :key="item">{{ item }}</span>
              </div>
            </div>
          </section>

          <section class="wecom-detail-section" aria-label="审批详情">
            <div
              v-for="row in primaryDetailRows"
              :key="row.label"
              class="wecom-detail-row"
              :class="{ 'wecom-detail-row--with-helper': row.helper }"
            >
              <div class="wecom-detail-label">{{ row.label }}</div>
              <div
                v-if="isAttachmentRow(row) && attachmentLinks.length"
                class="wecom-detail-attachments"
              >
                <button
                  v-for="item in attachmentLinks"
                  :key="`inline-${item.url}`"
                  type="button"
                  class="wecom-detail-attachment"
                  @click="openProofUrl(item.url)"
                >
                  <span class="wecom-detail-attachment__thumb" :class="{ 'wecom-detail-attachment__thumb--file': !isImageAttachment(item) }">
                    <img
                      v-if="isImageAttachment(item)"
                      :src="attachmentPreviewUrl(item)"
                      :alt="item.label"
                      loading="lazy"
                    />
                    <span v-else>{{ attachmentFileExtension(item) }}</span>
                  </span>
                  <span class="wecom-detail-attachment__name">{{ item.label }}</span>
                </button>
              </div>
              <div v-else class="wecom-detail-value" :class="{ 'wecom-detail-value--multiline': row.multiline }">
                {{ row.value }}
              </div>
              <div v-if="row.helper || row.helperAction" class="wecom-detail-helper">
                <span v-if="row.helper">{{ row.helper }}</span>
                <button v-if="row.helperAction" type="button" @click="row.action?.()">{{ row.helperAction }}</button>
              </div>
            </div>
          </section>

          <section v-if="secondaryDetailRows.length" class="wecom-detail-section wecom-detail-section--extra" aria-label="更多申请信息">
            <div
              v-for="row in secondaryDetailRows"
              :key="row.label"
              class="wecom-detail-row"
              :class="{ 'wecom-detail-row--with-helper': row.helper }"
            >
              <div class="wecom-detail-label">{{ row.label }}</div>
              <div
                v-if="isAttachmentRow(row) && attachmentLinks.length"
                class="wecom-detail-attachments"
              >
                <button
                  v-for="item in attachmentLinks"
                  :key="`secondary-${item.url}`"
                  type="button"
                  class="wecom-detail-attachment"
                  @click="openProofUrl(item.url)"
                >
                  <span class="wecom-detail-attachment__thumb" :class="{ 'wecom-detail-attachment__thumb--file': !isImageAttachment(item) }">
                    <img
                      v-if="isImageAttachment(item)"
                      :src="attachmentPreviewUrl(item)"
                      :alt="item.label"
                      loading="lazy"
                    />
                    <span v-else>{{ attachmentFileExtension(item) }}</span>
                  </span>
                  <span class="wecom-detail-attachment__name">{{ item.label }}</span>
                </button>
              </div>
              <div v-else class="wecom-detail-value" :class="{ 'wecom-detail-value--multiline': row.multiline }">
                {{ row.value }}
              </div>
              <div v-if="row.helper || row.helperAction" class="wecom-detail-helper">
                <span v-if="row.helper">{{ row.helper }}</span>
                <button v-if="row.helperAction" type="button" @click="row.action?.()">{{ row.helperAction }}</button>
              </div>
            </div>
          </section>

          <section v-if="attachmentLinks.length" class="wecom-attachment-section" aria-label="附件">
            <h2>附件</h2>
            <div class="wecom-attachment-grid">
              <button
                v-for="item in attachmentLinks"
                :key="item.url"
                type="button"
                class="wecom-attachment-card"
                @click="openProofUrl(item.url)"
              >
                <span class="wecom-attachment-thumb" :class="{ 'wecom-attachment-thumb--file': !isImageAttachment(item) }">
                  <img
                    v-if="isImageAttachment(item)"
                    :src="attachmentPreviewUrl(item)"
                    :alt="item.label"
                    loading="lazy"
                  />
                  <span v-else>{{ attachmentFileExtension(item) }}</span>
                </span>
                <span class="wecom-attachment-name">{{ item.label }}</span>
                <span class="wecom-attachment-action">查看</span>
              </button>
            </div>
          </section>

          <section v-if="progressNodes.length" class="wecom-flow-section" aria-label="审批进度">
            <h2>审批流程</h2>
            <div class="wecom-flow-list">
              <article
                v-for="node in progressNodes"
                :key="`${node.node_order}-${node.node_name}`"
                class="wecom-flow-step"
                :class="`wecom-flow-step--${node.status}`"
              >
                <div class="wecom-flow-rail">
                  <span class="wecom-flow-icon">
                    <ion-icon :icon="personOutline" />
                  </span>
                </div>
                <div class="wecom-flow-content">
                  <div class="wecom-flow-header">
                    <strong>{{ flowNodeHeader(node) }}</strong>
                    <span class="wecom-flow-caret" aria-hidden="true"></span>
                  </div>
                  <div class="wecom-flow-people">
                    <div class="wecom-flow-person-avatar">
                      <span>{{ approverInitial(node) }}</span>
                      <span v-if="isCompletedNode(node)" class="wecom-flow-person-check">
                        <ion-icon :icon="checkmarkOutline" />
                      </span>
                    </div>
                    <div class="wecom-flow-person-name">{{ node.approver_display || node.resolve_message || '提交后自动匹配' }}</div>
                    <div class="wecom-flow-person-action">{{ flowNodeActionText(node) }}</div>
                  </div>
                  <div v-if="flowNodeMetaItems(node).length" class="wecom-flow-meta">
                    <span
                      v-for="item in flowNodeMetaItems(node)"
                      :key="`${node.node_order}-${item}`"
                    >
                      {{ item }}
                    </span>
                  </div>
                  <div v-if="node.comment" class="wecom-flow-comment">{{ node.comment }}</div>
                </div>
              </article>
            </div>
          </section>

          <section v-else class="wecom-flow-section" aria-label="审批进度">
            <h2>审批流程</h2>
            <p class="wecom-empty-text">当前还没有审批流程记录。</p>
          </section>

          <section v-if="canRemind" class="wecom-remind-section" aria-label="审批催办">
            <ion-button
              expand="block"
              fill="outline"
              class="wecom-remind-button"
              :disabled="remindSubmitting"
              @click="sendApprovalReminder"
            >
              催办
            </ion-button>
          </section>

          <section v-if="canAct || canWithdraw" class="wecom-action-section" aria-label="移动处理">
            <h2>移动处理</h2>
            <div v-if="approvalCommentPresets.length" class="preset-chip-row">
              <button
                v-for="preset in approvalCommentPresets"
                :key="preset"
                class="preset-chip"
                type="button"
                @click="comment = preset"
              >
                {{ preset }}
              </button>
            </div>
            <ion-textarea
              v-model="comment"
              auto-grow
              fill="outline"
              label="审批意见"
              label-placement="stacked"
              placeholder="可填写通过、驳回或撤回说明"
            />
            <div v-if="canAct" class="wecom-action-buttons">
              <ion-button expand="block" :disabled="submitting" @click="processApproval('approve')">通过</ion-button>
              <ion-button expand="block" color="danger" fill="outline" :disabled="submitting" @click="processApproval('reject')">
                拒绝
              </ion-button>
            </div>
            <ion-button
              v-if="canWithdraw"
              expand="block"
              color="medium"
              fill="outline"
              class="wecom-withdraw-button"
              :disabled="submitting"
              @click="withdrawApproval"
            >
              撤回申请
            </ion-button>
          </section>
        </main>

        <main v-else class="wecom-detail-main">
          <section class="wecom-loading-section">正在加载审批详情</section>
        </main>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IonButton, IonContent, IonIcon, IonPage, IonTextarea, onIonViewWillEnter, toastController } from '@ionic/vue'
import { checkmarkOutline, chevronBackOutline, downloadOutline, personOutline, shareOutline } from 'ionicons/icons'
import { get, post, resolveAuthenticatedAssetUrl } from '@/utils/request'
import { formatCurrency, formatDate, formatDateTime, formatStatus } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useMobileInbox } from '@/composables/useMobileInbox'

type DetailRow = {
  label: string
  value: string
  multiline?: boolean
  helper?: string
  helperAction?: string
  action?: () => void | Promise<void>
}
type AttachmentLink = {
  label: string
  url: string
  contentType?: string
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const inbox = useMobileInbox(
  () => auth.displayRole !== 'employee',
  () => auth.roles.includes('hr') || auth.roles.includes('finance'),
)
const instanceId = computed(() => Number(route.params.id))
const mode = computed(() => String(route.query.mode || ''))
const detail = ref<any | null>(null)
const leaveRequest = ref<any | null>(null)
const overtimeRequest = ref<any | null>(null)
const recruitmentDemand = ref<any | null>(null)
const salaryAdjustmentRecord = ref<any | null>(null)
const comment = ref('')
const submitting = ref(false)
const remindSubmitting = ref(false)

const canAct = computed(() => mode.value === 'pending' && detail.value?.status === 'pending')
const canWithdraw = computed(() => mode.value === 'submitted' && detail.value?.status === 'pending')
const isCurrentApplicant = computed(() => {
  const applicantId = Number(detail.value?.applicant_id || 0)
  const currentUserId = Number(auth.user?.id || 0)
  return applicantId > 0 && applicantId === currentUserId
})
const canRemind = computed(() => detail.value?.status === 'pending' && isCurrentApplicant.value)
const formData = computed<Record<string, any>>(() => {
  const value = detail.value?.form_data
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
})
const approvalTitle = computed(() => {
  const title = detail.value?.summary || detail.value?.title || formData.value.summary || formData.value.reason
  return String(title || `${approvalTypeText.value}申请`).trim()
})
const approvalTypeText = computed(() => {
  if (isBusinessTripDetail.value) return '出差审批'
  if (isPunchCorrectionDetail.value) return '补卡审批'
  if (isLeaveLikeForm.value) return '请假审批'
  if (isOvertimeLikeForm.value) return '加班审批'
  const displayName = String(detail.value?.approval_type_name || '').trim()
  if (displayName.includes('招聘')) return '招聘审批'
  if (displayName.includes('出差')) return '出差审批'
  if (displayName.includes('补卡')) return '补卡审批'
  if (displayName.includes('请假')) return '请假审批'
  if (displayName.includes('加班')) return '加班审批'
  if (displayName.includes('调薪')) return '调薪审批'
  const rawText = String(detail.value?.business_type || detail.value?.module || '')
  const raw = rawText.toLowerCase()
  const map: Record<string, string> = {
    leave: '请假审批',
    qingjia: '请假审批',
    overtime: '加班审批',
    holiday_overtime: '加班审批',
    legal_overtime: '加班审批',
    business_trip: '出差审批',
    travel: '出差审批',
    punch_correction: '补卡审批',
    attendance_punch_correction: '补卡审批',
    salary_adjust: '调薪审批',
    salary_adjustment: '调薪审批',
    recruitment: '招聘审批',
  }
  if (rawText.includes('招聘')) return '招聘审批'
  return map[raw] || '审批申请'
})
const approvalPlainName = computed(() => {
  const directName = String(detail.value?.approval_type_name || '').trim()
  const source = directName || approvalTypeText.value
  const normalized = source.replace(/(审批|申请)$/g, '').trim()
  return normalized || '审批'
})
const approvalDetailPageTitle = computed(() => `${approvalPlainName.value}详情`)
const applicantAvatarLetter = computed(() => {
  const text = applicantText.value.trim()
  return text && text !== '-' ? text.slice(0, 1) : '申'
})
const statusBadgeText = computed(() => approvalStatusLabel(detail.value?.status))
const statusTone = computed(() => {
  const status = String(detail.value?.status || '').toLowerCase()
  if (status === 'approved' || status === 'completed') return 'success'
  if (status === 'rejected') return 'danger'
  if (status === 'withdrawn' || status === 'cancelled') return 'muted'
  return 'pending'
})
const approvalNumber = computed(() => {
  const explicit = coalesceText(
    detail.value?.approval_no,
    detail.value?.approval_number,
    detail.value?.instance_no,
    detail.value?.serial_no,
    formData.value.approval_no,
    formData.value.approval_number,
  )
  if (explicit) return explicit
  const day = compactDate(detail.value?.created_at)
  const id = Number(detail.value?.id || instanceId.value || 0)
  return `${day}${String(id).padStart(4, '0')}`
})
const isLeaveDetail = computed(() => {
  return Boolean(leaveRequest.value || isLeaveApproval.value || isLeaveLikeForm.value || approvalPlainName.value.includes('请假'))
})
const isOvertimeDetail = computed(() => {
  return Boolean(overtimeRequest.value || isOvertimeApproval.value || isOvertimeLikeForm.value || approvalPlainName.value.includes('加班'))
})
const isBusinessTripDetail = computed(() => {
  const moduleValue = String(detail.value?.module || '').toLowerCase()
  const businessTypeValue = String(detail.value?.business_type || '').toLowerCase()
  const name = String(detail.value?.approval_type_name || '')
  return moduleValue.includes('business_trip')
    || businessTypeValue.includes('business_trip')
    || businessTypeValue.includes('travel')
    || name.includes('出差')
    || Object.keys(formData.value).some((key) => key.toLowerCase().startsWith('trip_'))
})
const isPunchCorrectionDetail = computed(() => {
  const moduleValue = String(detail.value?.module || '').toLowerCase()
  const businessTypeValue = String(detail.value?.business_type || '').toLowerCase()
  const name = String(detail.value?.approval_type_name || '')
  return moduleValue.includes('punch_correction')
    || businessTypeValue.includes('punch_correction')
    || name.includes('补卡')
    || Object.keys(formData.value).some((key) => key.toLowerCase().includes('punch_correction'))
})
const departmentText = computed(() => coalesceText(
  detail.value?.department_name,
  detail.value?.applicant_department,
  displayValueForKeys(['department_name', 'department', 'dept_name', 'dept', 'applicant_department', 'apply_department', 'apply_dept', 'department_id', 'apply_department_id']),
) || '-')
const companyText = computed(() => coalesceText(
  detail.value?.company_name,
  detail.value?.organization_name,
  displayValueForKeys(['company_name', 'company', 'corp_name', 'organization_name', 'applicant_company', 'apply_company', 'company_id']),
) || '-')
const applicantMetaItems = computed(() => {
  const items: string[] = []
  if (departmentText.value && departmentText.value !== '-') items.push(`部门：${departmentText.value}`)
  const submittedAt = formatWecomDateTime(detail.value?.created_at)
  if (submittedAt && submittedAt !== '-') items.push(`提交时间：${submittedAt}`)
  return items
})
const leaveTypeText = computed(() => {
  if (leaveRequest.value) return leaveTypeName.value
  return displayValueForKeys(['leave_type', 'leave_type_name', 'leave_kind', 'vacation_type', 'type']) || '-'
})
const leaveStartText = computed(() => {
  if (leaveRequest.value) return `${formatWecomDate(leaveRequest.value.start_date)} ${halfDayLabel(leaveRequest.value.start_half, 'start')}`
  return formatLeaveMoment(displayValueForKeys(['leave_start_time', 'leave_start_date', 'start_time', 'start_date', 'begin_time', 'begin_date', 'from_time', 'from_date']), 'start')
})
const leaveEndText = computed(() => {
  if (leaveRequest.value) return `${formatWecomDate(leaveRequest.value.end_date)} ${halfDayLabel(leaveRequest.value.end_half, 'end')}`
  return formatLeaveMoment(displayValueForKeys(['leave_end_time', 'leave_end_date', 'end_time', 'end_date', 'finish_time', 'finish_date', 'to_time', 'to_date']), 'end')
})
const leaveDurationText = computed(() => {
  if (leaveRequest.value?.days !== undefined && leaveRequest.value?.days !== null) return `${leaveRequest.value.days}天`
  const value = displayValueForKeys(['leave_duration', 'duration', 'days', 'day_count', 'hours', 'hour_count', 'total_duration'])
  if (!value) return '-'
  if (/[天小时]/.test(value)) return value
  return String(value).includes('.') || Number(value) > 8 ? `${value}小时` : `${value}天`
})
const attachmentText = computed(() => {
  if (attachmentLinks.value.length) return attachmentLinks.value.map((item) => item.label).join('、')
  const value = displayValueForKeys(['attachment', 'attachments', 'proof_url', 'file', 'files'])
  if (!value || value === 'false') return '无'
  if (/^https?:\/\//i.test(value)) return '附件'
  return value
})
const primaryDetailRows = computed<DetailRow[]>(() => {
  const rows: DetailRow[] = [
    { label: '审批编号', value: approvalNumber.value },
    { label: '提交时间', value: formatWecomDateTime(detail.value?.created_at) },
  ]

  if (isLeaveDetail.value) {
    rows.push(
      { label: '所在部门', value: departmentText.value },
      { label: '所在公司', value: companyText.value },
      { label: '请假类型', value: leaveTypeText.value },
      { label: '开始时间', value: leaveStartText.value },
      { label: '结束时间', value: leaveEndText.value },
      {
        label: '请假时长',
        value: leaveDurationText.value,
        helper: '当前请假时长为自动计算，',
        helperAction: '查看时长明细',
      },
      { label: '请假事由', value: approvalReason.value || '-' },
      {
        label: '说明附件',
        value: attachmentText.value,
        action: attachmentLinks.value[0]?.url ? () => openProofUrl(attachmentLinks.value[0]?.url) : undefined,
        helperAction: attachmentLinks.value[0]?.url ? '查看附件' : undefined,
      },
    )
    return rows
  }

  if (isBusinessTripDetail.value) {
    rows.push(
      { label: '所在部门', value: departmentText.value },
      { label: '所在公司', value: companyText.value },
      ...businessTripDetailRows.value,
      {
        label: '说明附件',
        value: attachmentText.value,
        action: attachmentLinks.value[0]?.url ? () => openProofUrl(attachmentLinks.value[0]?.url) : undefined,
        helperAction: attachmentLinks.value[0]?.url ? '查看附件' : undefined,
      },
    )
    return rows
  }

  if (isPunchCorrectionDetail.value) {
    rows.push(
      { label: '所在部门', value: departmentText.value },
      { label: '所在公司', value: companyText.value },
      ...punchCorrectionDetailRows.value,
      {
        label: '说明附件',
        value: attachmentText.value,
        action: attachmentLinks.value[0]?.url ? () => openProofUrl(attachmentLinks.value[0]?.url) : undefined,
        helperAction: attachmentLinks.value[0]?.url ? '查看附件' : undefined,
      },
    )
    return rows
  }

  if (isOvertimeDetail.value) {
    rows.push(
      { label: '所在部门', value: departmentText.value },
      { label: '所在公司', value: companyText.value },
      ...overtimeDetailRows.value,
      {
        label: '说明附件',
        value: attachmentText.value,
        action: attachmentLinks.value[0]?.url ? () => openProofUrl(attachmentLinks.value[0]?.url) : undefined,
        helperAction: attachmentLinks.value[0]?.url ? '查看附件' : undefined,
      },
    )
    return rows
  }

  rows.push(
    { label: '申请内容', value: approvalTitle.value || '-' },
    { label: '申请类型', value: approvalTypeText.value },
    { label: '申请人', value: applicantText.value },
    { label: '所在部门', value: departmentText.value },
    { label: '所在公司', value: companyText.value },
  )
  if (approvalReason.value) {
    rows.push({ label: '说明', value: approvalReason.value, multiline: approvalReason.value.length > 20 })
  }
  rows.push({
    label: '说明附件',
    value: attachmentText.value,
    action: attachmentLinks.value[0]?.url ? () => openProofUrl(attachmentLinks.value[0]?.url) : undefined,
    helperAction: attachmentLinks.value[0]?.url ? '查看附件' : undefined,
  })
  return rows
})
const secondaryDetailRows = computed<DetailRow[]>(() => {
  const primaryLabels = new Set(primaryDetailRows.value.map((row) => row.label))
  return genericSummaryItems.value
    .filter((item) => !primaryLabels.has(item.label))
    .slice(0, 8)
    .map((item) => ({
      label: item.label,
      value: item.value,
      multiline: item.multiline,
    }))
})
const heroEyebrow = computed(() => {
  const status = String(detail.value?.status || '').toLowerCase()
  if (status === 'approved') return '已通过'
  if (status === 'rejected') return '已拒绝'
  if (status === 'withdrawn' || status === 'cancelled') return '已结束'
  return '已提交'
})
const statusSentence = computed(() => {
  const status = String(detail.value?.status || '').toLowerCase()
  if (status === 'pending') return '申请已提交，正在等待审批。'
  if (status === 'approved') return '审批已通过。'
  if (status === 'rejected') return '审批未通过，请查看下方意见。'
  if (status === 'withdrawn') return '申请已撤回。'
  if (status === 'cancelled') return '申请已取消。'
  return formatStatus(status)
})
const nextStepText = computed(() => {
  const status = String(detail.value?.status || '').toLowerCase()
  if (status === 'pending') return '等审批'
  if (status === 'approved') return '已完成'
  if (status === 'rejected') return '看意见'
  return '已结束'
})
const applicantText = computed(() => {
  return detail.value?.applicant_name || detail.value?.submitter_name || (detail.value?.applicant_id ? `员工 #${detail.value.applicant_id}` : '-')
})
const approvalReason = computed(() => {
  const candidates = [
    formData.value.reason,
    formData.value.remark,
    formData.value.note,
    formData.value.trip_reason,
    formData.value.punch_reason,
    formData.value.overtime_reason,
    formData.value.description,
    detail.value?.content,
  ]
  const value = candidates.find((item) => item !== undefined && item !== null && String(item).trim())
  return value ? String(value).trim() : ''
})
const isOvertimeLikeForm = computed(() => {
  if (isLeaveLikeForm.value || isBusinessTripDetail.value || isPunchCorrectionDetail.value) return false
  const keys = Object.keys(formData.value).map((key) => key.toLowerCase())
  return keys.some((key) => ['overtime_hours', 'overtime_date', 'work_date', 'overtime_duration'].includes(key))
    || String(detail.value?.business_type || detail.value?.module || '').toLowerCase().includes('overtime')
})
const isLeaveLikeForm = computed(() => {
  const keys = Object.keys(formData.value).map((key) => key.toLowerCase())
  return keys.some((key) => ['leave_type', 'leave_type_id', 'start_date', 'end_date', 'days', 'leave_duration'].includes(key))
})
const isLeaveApproval = computed(() => {
  const moduleValue = String(detail.value?.module || '').toLowerCase()
  const businessTypeValue = String(detail.value?.business_type || '').toLowerCase()
  return moduleValue === 'leave' || businessTypeValue.includes('leave')
})
const leaveTypeName = computed(() => {
  return leaveRequest.value?.leave_type_name || leaveRequest.value?.leave_type?.name || `假期类型 #${leaveRequest.value?.leave_type_id || '-'}`
})
const isOvertimeApproval = computed(() => {
  const moduleValue = String(detail.value?.module || '').toLowerCase()
  const businessTypeValue = String(detail.value?.business_type || '').toLowerCase()
  return moduleValue === 'overtime' || businessTypeValue.includes('overtime')
})
const isRecruitmentApproval = computed(() => {
  const moduleText = String(detail.value?.module || '')
  const businessTypeText = String(detail.value?.business_type || '')
  const typeText = String(detail.value?.approval_type_name || '')
  const moduleValue = moduleText.toLowerCase()
  const businessTypeValue = businessTypeText.toLowerCase()
  return moduleText.includes('招聘')
    || businessTypeText.includes('招聘')
    || typeText.includes('招聘')
    || moduleValue.includes('recruitment')
    || businessTypeValue.includes('recruitment')
})
const isSalaryAdjustmentApproval = computed(() => {
  const moduleValue = String(detail.value?.module || '').toLowerCase()
  const businessTypeValue = String(detail.value?.business_type || '').toLowerCase()
  return moduleValue.includes('salary') || businessTypeValue.includes('salary') || businessTypeValue.includes('payroll_adjust')
})
const genericSummaryTitle = computed(() => {
  if (Object.keys(formData.value).length) return '申请明细'
  const moduleValue = String(detail.value?.module || '').toLowerCase()
  const businessTypeValue = String(detail.value?.business_type || '').toLowerCase()
  if (moduleValue.includes('offboard') || businessTypeValue.includes('offboard')) return '离职业务摘要'
  if (moduleValue.includes('transfer') || businessTypeValue.includes('transfer')) return '调岗业务摘要'
  if (moduleValue.includes('training') || businessTypeValue.includes('training')) return '培训业务摘要'
  if (moduleValue.includes('asset') || businessTypeValue.includes('asset')) return '资产业务摘要'
  if (moduleValue.includes('payroll') || businessTypeValue.includes('payroll')) return '薪资批次摘要'
  return '业务补充摘要'
})
const recruitmentSalaryText = computed(() => {
  const min = recruitmentDemand.value?.salary_range_min
  const max = recruitmentDemand.value?.salary_range_max
  if (min && max) return `${min} ~ ${max}`
  if (min) return `${min} 起`
  if (max) return `最高 ${max}`
  return '未填写'
})
const attachmentLinks = computed(() => {
  const urlPattern = /(https?:\/\/[^\s"'，、]+|\/uploads\/[^\s"'，、]+)/gi
  const links: AttachmentLink[] = []
  const seen = new Set<string>()

  pushAttachmentLink(links, seen, leaveRequest.value?.proof_url, '请假证明材料')

  const backendLabels: Record<string, string> = detail.value?.form_field_labels || {}
  const backendFieldTypes: Record<string, string> = detail.value?.form_field_types || {}
  for (const [key, value] of Object.entries(formData.value)) {
    const fieldType = String(backendFieldTypes[key] || '').toLowerCase()
    if (fieldType === 'attachment' || /attach|file|proof|附件/.test(key)) {
      collectAttachmentLinks(value, backendLabels[key] || humanizeFieldName(key), links, seen)
    }
  }

  const content = String(detail.value?.content || '')
  const matched = content.match(urlPattern) || []
  matched.forEach((url, index) => pushAttachmentLink(links, seen, url, `说明附件 ${index + 1}`))

  const recruitmentContent = JSON.stringify(recruitmentDemand.value || {})
  const recruitmentMatched = recruitmentContent.match(urlPattern) || []
  recruitmentMatched.forEach((url, index) => pushAttachmentLink(links, seen, url, `招聘附件 ${index + 1}`))

  return links
})
function firstDisplayValue(keys: string[]) {
  return displayValueForKeys(keys) || ''
}

function pushDetailRow(rows: DetailRow[], label: string, value: unknown, options: Partial<DetailRow> = {}) {
  const normalized = normalizeDisplayValue(value)
  if (!normalized || normalized === '-') return
  rows.push({
    label,
    value: normalized,
    multiline: normalized.length > 28,
    ...options,
  })
}

const businessTripDetailRows = computed<DetailRow[]>(() => {
  const rows: DetailRow[] = []
  pushDetailRow(rows, '出差地点', firstDisplayValue(['trip_location', 'business_trip_location', 'destination', 'destination_city', 'city', 'location']))
  pushDetailRow(rows, '开始时间', formatFormDateTimeValue(firstDisplayValue(['trip_start_time', 'trip_start_date', 'business_trip_start_time', 'start_time', 'start_date', 'begin_time', 'begin_date'])))
  pushDetailRow(rows, '结束时间', formatFormDateTimeValue(firstDisplayValue(['trip_end_time', 'trip_end_date', 'business_trip_end_time', 'end_time', 'end_date', 'finish_time', 'finish_date'])))
  const duration = firstDisplayValue(['trip_duration', 'business_trip_duration', 'duration', 'days', 'day_count'])
  if (duration) rows.push({ label: '出差时长', value: /[天小时]/.test(duration) ? duration : `${duration}天` })
  pushDetailRow(rows, '出差事由', approvalReason.value || firstDisplayValue(['trip_reason', 'reason']), { multiline: true })
  return rows.length ? rows : genericSummaryItems.value
})

const punchCorrectionDetailRows = computed<DetailRow[]>(() => {
  const rows: DetailRow[] = []
  pushDetailRow(rows, '补卡日期', formatFormDateTimeValue(firstDisplayValue(['correction_date', 'target_date', 'punch_date', 'attendance_date', 'date'])))
  pushDetailRow(rows, '补卡班次', firstDisplayValue(['punch_correction_slot', 'punch_type', 'slot', 'shift', 'shift_name', 'attendance_shift']))
  pushDetailRow(rows, '补卡时间', formatFormDateTimeValue(firstDisplayValue(['punch_time', 'correction_time', 'target_time', 'attendance_time', 'datetime_3'])))
  pushDetailRow(rows, '补卡原因', approvalReason.value || firstDisplayValue(['punch_reason', 'correction_reason', 'reason', 'textarea_4']), { multiline: true })
  return rows.length ? rows : genericSummaryItems.value
})

const overtimeDetailRows = computed<DetailRow[]>(() => {
  if (overtimeRequest.value) {
    const rows: DetailRow[] = []
    const reason = coalesceText(overtimeRequest.value.reason, displayValueForKeys(['overtime_reason', 'reason']), approvalReason.value)
    const department = coalesceText(displayValueForKeys(['department_id', 'apply_department_id', 'department_name', 'department']), departmentText.value)
    const overtimeDate = normalizeDisplayValue(overtimeRequest.value.overtime_date)
    const start = formatOvertimeMoment(overtimeDate, overtimeRequest.value.start_time)
    const end = formatOvertimeMoment(overtimeDate, overtimeRequest.value.end_time)
    const duration = coalesceText(overtimeRequest.value.hours, displayValueForKeys(['hours', 'duration', 'overtime_hours']))
    const type = coalesceText(overtimeTypeLabel(overtimeRequest.value.overtime_type))

    if (reason) rows.push({ label: '加班事由', value: reason, multiline: reason.length > 28 })
    if (department && department !== '-') rows.push({ label: '申请部门', value: department })
    if (type && type !== '-') rows.push({ label: '加班类型', value: type })
    if (start && start !== '-') rows.push({ label: '开始时间', value: start })
    if (end && end !== '-') rows.push({ label: '结束时间', value: end })
    if (duration) rows.push({ label: '加班时长', value: decorateFieldValue('hours', duration, 'duration') })
    return rows
  }

  return genericSummaryItems.value.map((item) => ({
    label: item.label,
    value: item.value,
    multiline: item.multiline,
  }))
})
const genericSummaryItems = computed(() => {
  const raw = String(detail.value?.content || '').trim()
  if (leaveRequest.value || overtimeRequest.value || recruitmentDemand.value || salaryAdjustmentRecord.value) {
    return []
  }

  const labelMap: Record<string, string> = {
    summary: '申请摘要',
    title: '申请标题',
    work_date: '加班日期',
    overtime_date: '加班日期',
    start_time: '开始时间',
    end_time: '结束时间',
    hours: '加班时长',
    duration: '加班时长',
    overtime_hours: '加班时长',
    phone: '手机号',
    mobile: '手机号',
    start_date: '开始日期',
    end_date: '结束日期',
    days: '天数',
    leave_type: '请假类型',
    employee_name: '员工',
    department_name: '部门',
    position_name: '岗位',
    department: '部门',
    position: '岗位',
    leave_date: '离职日期',
    leave_reason: '离职原因',
    transfer_date: '调岗日期',
    training_name: '培训名称',
    training_topic: '培训主题',
    training_date: '培训日期',
    asset_name: '资产名称',
    asset_no: '资产编号',
    checkout_date: '领用日期',
    effective_date: '生效日期',
    year: '年度',
    month: '月份',
    gross_salary: '应发工资',
    net_salary: '实发工资',
    adjustment_amount: '调整金额',
    reason: '原因',
    note: '备注',
  }
  const backendLabels: Record<string, string> = detail.value?.form_field_labels || {}
  const backendDisplayValues: Record<string, string> = detail.value?.form_field_display_values || {}
  const backendFieldTypes: Record<string, string> = detail.value?.form_field_types || {}

  function normalizeValue(value: unknown): string | null {
    if (value == null) return null
    if (typeof value === 'string') return value.trim() || null
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
    if (Array.isArray(value)) return value.length ? value.join('、') : null
    if (typeof value === 'object') {
      const record = value as Record<string, any>
      const summary = record.summary
      if (summary && typeof summary === 'object' && !Array.isArray(summary)) {
        const total = summary.total
        if (total !== undefined && total !== null && String(total).trim() !== '') {
          const label = String(summary.label || '').trim()
          return `${label ? `${label} ` : ''}${total}`
        }
      }
      return null
    }
    return null
  }

  function pushSummaryRow(
    target: Array<{ label: string; value: string; multiline?: boolean }>,
    key: string,
    value: unknown,
    label?: string,
  ) {
    const fieldType = String(backendFieldTypes[key] || '').toLowerCase()
    if (fieldType === 'attachment' || /attach|file|proof/.test(key.toLowerCase())) return
    const normalized = backendDisplayValues[key] || normalizeValue(value)
    if (!normalized || /^https?:\/\//i.test(normalized)) return
    if (['summary', 'title'].includes(key) && normalized === approvalTitle.value) return
    const displayValue = decorateFieldValue(key, normalized, fieldType)
    target.push({
      label: label || backendLabels[key] || labelMap[key] || humanizeFieldName(key),
      value: displayValue,
      multiline: displayValue.length > 28,
    })
  }

  const summary: Array<{ label: string; value: string; multiline?: boolean }> = []
  for (const [key, value] of Object.entries(formData.value)) {
    const durationTimeMatch = key.match(/^(.+)_(start|end)_time$/i)
    if (durationTimeMatch && Object.prototype.hasOwnProperty.call(formData.value, durationTimeMatch[1])) {
      continue
    }

    const fieldType = String(backendFieldTypes[key] || '').toLowerCase()
    const hasDurationStart = Object.prototype.hasOwnProperty.call(formData.value, `${key}_start_time`)
    const hasDurationEnd = Object.prototype.hasOwnProperty.call(formData.value, `${key}_end_time`)
    if (fieldType === 'duration' || hasDurationStart || hasDurationEnd) {
      if (hasDurationStart) pushSummaryRow(summary, `${key}_start_time`, formData.value[`${key}_start_time`], '开始时间')
      if (hasDurationEnd) pushSummaryRow(summary, `${key}_end_time`, formData.value[`${key}_end_time`], '结束时间')
      pushSummaryRow(summary, key, value, backendLabels[key] || labelMap[key] || '时长')
      continue
    }

    pushSummaryRow(summary, key, value)
  }

  if (summary.length) return summary.slice(0, 8)
  if (!raw) return []

  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const [key, value] of Object.entries(parsed)) {
        const normalized = normalizeValue(value)
        if (!normalized || /^https?:\/\//i.test(normalized)) continue
        summary.push({
          label: labelMap[key] || key,
          value: normalized,
          multiline: normalized.length > 28,
        })
      }
    }
  } catch {
    const lines = raw
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    for (const line of lines) {
      const matched = line.match(/^([^:：]{1,20})[:：]\s*(.+)$/)
      if (!matched) continue
      summary.push({
        label: matched[1],
        value: matched[2],
        multiline: matched[2].length > 28,
      })
    }
  }

  if (!summary.length && raw.length <= 120) {
    summary.push({
      label: '业务说明',
      value: raw,
      multiline: true,
    })
  }

  return summary.slice(0, 8)
})
const approvalCommentPresets = computed(() => {
  if (canWithdraw.value) {
    return ['移动端撤回，改为网页端继续处理', '信息需调整，先撤回后重提']
  }
  if (!canAct.value) return []
  return ['同意，按流程执行', '信息已核对，可继续审批', '资料不完整，请补充后重提']
})
const progressNodes = computed<any[]>(() => Array.isArray(detail.value?.progress_nodes) ? detail.value.progress_nodes : [])

function progressStatusLabel(status?: string) {
  const map: Record<string, string> = {
    completed: '已通过',
    pending: '待审批',
    not_started: '未开始',
    rejected: '已拒绝',
    withdrawn: '已撤回',
    auto_approved: '自动通过',
    notified: '已抄送',
    skipped: '已跳过',
  }
  return map[String(status || '')] || formatStatus(status || '')
}

function approvalStatusLabel(status?: string | null) {
  const map: Record<string, string> = {
    approved: '已通过',
    completed: '已通过',
    pending: '待审批',
    rejected: '已驳回',
    withdrawn: '已撤回',
    cancelled: '已撤回',
    canceled: '已撤回',
  }
  return map[String(status || '').toLowerCase()] || formatStatus(status || '')
}

function coalesceText(...values: unknown[]) {
  for (const value of values) {
    const normalized = normalizeDisplayValue(value)
    if (normalized) return normalized
  }
  return ''
}

function normalizeDisplayValue(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value.map((item) => normalizeDisplayValue(item)).filter(Boolean).join('、')
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    return coalesceText(record.display, record.label, record.name, record.text, record.value)
  }
  return ''
}

function fileNameFromUrl(url?: string | null) {
  if (!url) return ''
  const pathname = String(url).split('?')[0].split('#')[0]
  const name = pathname.split('/').filter(Boolean).pop() || ''
  try {
    return decodeURIComponent(name)
  } catch {
    return name
  }
}

function isAttachmentUrl(value: string) {
  return /^https?:\/\//i.test(value) || /^\/?uploads\//i.test(value)
}

function pushAttachmentLink(
  target: AttachmentLink[],
  seen: Set<string>,
  url?: string | null,
  label = '附件',
  contentType = '',
) {
  const normalized = String(url || '').trim()
  if (!normalized || !isAttachmentUrl(normalized) || seen.has(normalized)) return
  seen.add(normalized)
  target.push({ label, url: normalized, contentType })
}

function attachmentLabelFromRecord(record: Record<string, unknown>, fallback: string, index: number) {
  const label = coalesceText(record.name, record.filename, record.original_filename, record.label)
  return label || `${fallback || '附件'} ${index + 1}`
}

function collectAttachmentLinks(
  value: unknown,
  label: string,
  target: AttachmentLink[],
  seen: Set<string>,
) {
  if (!value) return
  if (typeof value === 'string') {
    pushAttachmentLink(target, seen, value, label || fileNameFromUrl(value) || '附件')
    return
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectAttachmentLinks(item, `${label || '附件'} ${index + 1}`, target, seen))
    return
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const url = coalesceText(record.url, record.path, record.file_url)
    if (url) {
      pushAttachmentLink(
        target,
        seen,
        url,
        attachmentLabelFromRecord(record, label, target.length),
        coalesceText(record.content_type, record.type),
      )
      return
    }
    for (const nested of Object.values(record)) {
      collectAttachmentLinks(nested, label, target, seen)
    }
  }
}

function isAttachmentRow(row: DetailRow) {
  return /附件|证明|材料/.test(row.label)
}

function attachmentPreviewUrl(item: AttachmentLink) {
  return resolveAuthenticatedAssetUrl(item.url)
}

function isImageAttachment(item: AttachmentLink) {
  const contentType = String(item.contentType || '').toLowerCase()
  if (contentType.startsWith('image/')) return true
  return /\.(png|jpe?g|gif|webp|bmp|svg|heic|heif)(\?|#|$)/i.test(item.url)
}

function attachmentFileExtension(item: AttachmentLink) {
  const name = `${item.label || ''} ${item.url || ''}`
  const matched = name.match(/\.([a-z0-9]{1,6})(?:[?#\s]|$)/i)
  return matched ? matched[1].toUpperCase() : 'FILE'
}

function displayValueForKeys(keys: string[]) {
  const displayValues: Record<string, unknown> = detail.value?.form_field_display_values || {}
  for (const key of keys) {
    const normalized = coalesceText(displayValues[key], formData.value[key])
    if (normalized) return normalized
  }
  return ''
}

function formatWecomDate(value?: string | null) {
  if (!value) return '-'
  const raw = String(value).trim()
  const matched = raw.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (matched) return `${matched[1]}/${Number(matched[2])}/${Number(matched[3])}`
  const fallback = formatDate(value)
  const fallbackMatched = fallback.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (fallbackMatched) return `${fallbackMatched[1]}/${Number(fallbackMatched[2])}/${Number(fallbackMatched[3])}`
  return raw
}

function formatWecomDateTime(value?: string | null) {
  if (!value) return '-'
  const formatted = formatDateTime(value)
  const matched = formatted.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}:\d{2})/)
  if (matched) return `${matched[1]}/${Number(matched[2])}/${Number(matched[3])} ${matched[4]}`
  return formatted.replace(/-/g, '/')
}

function compactDate(value?: string | null) {
  const date = formatWecomDate(value)
  const matched = date.match(/(\d{4})\/(\d{1,2})\/(\d{1,2})/)
  if (!matched) return '00000000'
  return `${matched[1]}${String(Number(matched[2])).padStart(2, '0')}${String(Number(matched[3])).padStart(2, '0')}`
}

function shortWecomDateTime(value?: string | null) {
  const full = formatWecomDateTime(value)
  const matched = full.match(/\d{4}\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}:\d{2})/)
  if (!matched) return full
  return `${Number(matched[1])}/${Number(matched[2])} ${matched[3]}`
}

function halfDayLabel(value: unknown, fallback: 'start' | 'end') {
  const text = String(value || '').toLowerCase()
  if (text.includes('下午') || text === 'pm' || text === 'p.m.') return '下午'
  if (text.includes('上午') || text === 'am' || text === 'a.m.') return '上午'
  return fallback === 'start' ? '上午' : '下午'
}

function formatLeaveMoment(value: string, fallback: 'start' | 'end') {
  if (!value) return '-'
  const raw = String(value).trim()
  const explicitPeriod = raw.includes('下午') ? '下午' : (raw.includes('上午') ? '上午' : '')
  const timeMatched = raw.match(/(?:T|\s)(\d{1,2}):\d{2}/)
  const inferredPeriod = explicitPeriod || (timeMatched ? (Number(timeMatched[1]) >= 12 ? '下午' : '上午') : halfDayLabel('', fallback))
  return `${formatWecomDate(raw)} ${inferredPeriod}`
}

function flowNodeHeader(node: any) {
  const name = node?.node_name || node?.node_type_label || '审批节点'
  return `${name} · ${node?.status_label || progressStatusLabel(node?.status)}`
}

function approverInitial(node: any) {
  const name = coalesceText(node?.approver_display, node?.resolve_message, '审')
  return name.slice(0, 1)
}

function isCompletedNode(node: any) {
  return ['completed', 'approved', 'auto_approved', 'notified'].includes(String(node?.status || '').toLowerCase())
}

function flowNodeActionText(node: any) {
  const label = node?.status_label || progressStatusLabel(node?.status)
  return node?.acted_at ? `${label} · ${shortWecomDateTime(node.acted_at)}` : label
}

function flowNodeMetaItems(node: any) {
  const items: string[] = []
  const timeInfo = flowNodeTimeInfo(node)
  if (timeInfo?.value && timeInfo.value !== '-') items.push(`${timeInfo.label}：${timeInfo.value}`)
  const department = flowNodeDepartmentText(node)
  if (department && department !== '-') items.push(`部门：${department}`)
  return items
}

function flowNodeTimeInfo(node: any): { label: string; value: string } | null {
  const status = String(node?.status || '').toLowerCase()
  const actedAt = coalesceText(node?.acted_at, node?.completed_at, node?.action_at)
  if (actedAt) return { label: '处理时间', value: formatWecomDateTime(actedAt) }

  const arrivedAt = coalesceText(node?.arrived_at, node?.pending_at, node?.created_at)
  if (arrivedAt) {
    return { label: status === 'not_started' || status === 'skipped' ? '提交时间' : '到达时间', value: formatWecomDateTime(arrivedAt) }
  }

  if (status === 'pending') {
    const fallback = coalesceText(detail.value?.updated_at, detail.value?.created_at)
    if (fallback) return { label: '到达时间', value: formatWecomDateTime(fallback) }
  }

  if (Number(node?.node_order || 0) === 1) {
    const submittedAt = coalesceText(detail.value?.created_at)
    if (submittedAt) return { label: '提交时间', value: formatWecomDateTime(submittedAt) }
  }
  return null
}

function flowNodeDepartmentText(node: any) {
  return coalesceText(
    node?.approver_department_name,
    node?.department_name,
    node?.approver_department,
    node?.department,
    node?.applicant_department_name,
    departmentText.value,
  )
}

function humanizeFieldName(key: string) {
  return String(key || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function decorateFieldValue(key: string, value: string, fieldType = '') {
  const normalizedKey = String(key || '').toLowerCase()
  if (isDateTimeFieldKey(normalizedKey)) {
    return formatFormDateTimeValue(value)
  }
  if (
    (fieldType === 'duration' || ['hours', 'duration', 'overtime_hours'].includes(normalizedKey) || normalizedKey.includes('duration'))
    && !/[小时天]/.test(value)
  ) {
    return `${value} 小时`
  }
  return value
}

function isDateTimeFieldKey(key: string) {
  return /(^|_)(start|end|begin|finish)_time$/.test(key)
    || /(^|_)time($|_)/.test(key)
    || key === 'date'
    || key.endsWith('_date')
    || key.includes('_date_')
}

function formatFormDateTimeValue(value: string) {
  const raw = String(value || '').trim()
  const matched = raw.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s]+(\d{1,2}):(\d{2}))?/)
  if (!matched) return raw.replace('T', ' ')
  const dateText = `${matched[1]}/${Number(matched[2])}/${Number(matched[3])}`
  if (!matched[4]) return dateText
  return `${dateText} ${String(Number(matched[4])).padStart(2, '0')}:${matched[5]}`
}

function formatOvertimeMoment(dateValue: string, timeValue: unknown) {
  const dateText = formatFormDateTimeValue(dateValue)
  const timeText = normalizeDisplayValue(timeValue)
  if (!dateText || dateText === '-') return timeText || '-'
  if (!timeText) return dateText
  if (/^\d{4}[-/]/.test(timeText)) return formatFormDateTimeValue(timeText)
  return `${dateText} ${timeText.slice(0, 5)}`
}

function recordTitle(record: any) {
  const action = String(record?.action || '').toLowerCase()
  if (action === 'submit') return '已提交申请'
  if (action === 'approve') return '审批通过'
  if (action === 'reject') return '审批拒绝'
  if (action === 'withdraw') return '申请撤回'
  if (action === 'transfer') return '转交处理'
  if (action === 'auto_approve') return '系统自动通过'
  if (action === 'auto_skip') return '系统自动跳过'
  return formatStatus(action)
}

function recordActor(record: any) {
  const action = String(record?.action || '').toLowerCase()
  if (action === 'submit' || record?.node_order === 0) return '申请人'
  return record?.approver_name || '审批人'
}

function recordComment(record: any) {
  const comment = String(record?.comment || '').trim()
  if (!comment || comment === approvalTitle.value) return ''
  return comment
}

async function loadDetail() {
  detail.value = await get(`/approval/instances/${instanceId.value}`)
  leaveRequest.value = null
  overtimeRequest.value = null
  recruitmentDemand.value = null
  salaryAdjustmentRecord.value = null
  if (!detail.value?.business_id) return

  if (isLeaveApproval.value) {
    try {
      leaveRequest.value = await get(`/leave/requests/${detail.value.business_id}`)
    } catch {
      leaveRequest.value = null
    }
  }
  if (isOvertimeApproval.value) {
    try {
      overtimeRequest.value = await get(`/overtime/${detail.value.business_id}`)
    } catch {
      overtimeRequest.value = null
    }
  }
  if (isRecruitmentApproval.value) {
    try {
      recruitmentDemand.value = await get(`/recruitment/demands/${detail.value.business_id}`)
    } catch {
      recruitmentDemand.value = null
    }
  }
  if (isSalaryAdjustmentApproval.value) {
    try {
      salaryAdjustmentRecord.value = await get(`/payroll/salary-records/${detail.value.business_id}`)
    } catch {
      salaryAdjustmentRecord.value = null
    }
  }
}

async function refreshDetailAndInbox() {
  await loadDetail()
  try {
    await inbox.loadInboxCounts()
  } catch {
    // 详情已刷新成功时，角标刷新失败不阻断用户查看审批。
  }
}

async function openProofUrl(url?: string | null) {
  if (!url) return
  const resolved = resolveAuthenticatedAssetUrl(url)
  if (!resolved) return
  window.open(resolved, '_blank', 'noopener,noreferrer')
}

async function presentDetailToast(message: string, color: 'success' | 'warning' | 'danger' | 'medium' = 'medium') {
  const toast = await toastController.create({
    message,
    duration: 1600,
    color,
  })
  await toast.present()
}

async function exportApprovalPdf() {
  if (!detail.value) {
    await presentDetailToast('审批详情仍在加载，请稍后再试', 'warning')
    return
  }
  window.print()
}

async function shareApprovalDetail() {
  const url = window.location.href
  const title = approvalDetailPageTitle.value
  try {
    if (navigator.share) {
      await navigator.share({ title, text: approvalTitle.value, url })
      return
    }
    await navigator.clipboard?.writeText(url)
    await presentDetailToast('审批详情链接已复制', 'success')
  } catch {
    await presentDetailToast('分享失败，请稍后重试', 'warning')
  }
}

function overtimeTypeLabel(type?: string | null) {
  const map: Record<string, string> = {
    weekday: '工作日加班',
    weekend: '休息日加班',
    holiday: '法定节假日加班',
  }
  return map[String(type || '').toLowerCase()] || type || '-'
}

async function processApproval(action: 'approve' | 'reject') {
  submitting.value = true
  try {
    await post(`/approval/instances/${instanceId.value}/process`, {
      action,
      comment: comment.value.trim(),
    })
    comment.value = ''
    await refreshDetailAndInbox()
    const toast = await toastController.create({
      message: action === 'approve' ? '审批已通过' : '审批已拒绝',
      duration: 1600,
      color: action === 'approve' ? 'success' : 'warning',
    })
    await toast.present()
  } finally {
    submitting.value = false
  }
}

async function withdrawApproval() {
  submitting.value = true
  try {
    await post(`/approval/instances/${instanceId.value}/withdraw`)
    comment.value = ''
    await refreshDetailAndInbox()
    const toast = await toastController.create({
      message: '申请已撤回',
      duration: 1600,
      color: 'medium',
    })
    await toast.present()
  } finally {
    submitting.value = false
  }
}

async function sendApprovalReminder() {
  remindSubmitting.value = true
  try {
    const result = await post<any>(`/approval/instances/${instanceId.value}/remind`, {})
    const count = Number(result?.notified_count || result?.notified_approver_ids?.length || 0)
    const toast = await toastController.create({
      message: count > 0 ? `已催办 ${count} 人` : '催办提醒已发送',
      duration: 1600,
      color: 'success',
    })
    await toast.present()
  } finally {
    remindSubmitting.value = false
  }
}

onIonViewWillEnter(refreshDetailAndInbox)
</script>

<style scoped>
.wecom-detail-content {
  --background: #f6f7f9;
}

.wecom-approval-detail-page {
  min-height: 100%;
  color: #111111;
  background: #f6f7f9;
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}

.wecom-detail-nav {
  position: sticky;
  top: 0;
  z-index: 10;
  height: calc(56px + env(safe-area-inset-top));
  padding: env(safe-area-inset-top) 14px 0;
  display: grid;
  grid-template-columns: 78px minmax(0, 1fr) 78px;
  align-items: center;
  background: rgba(255, 255, 255, 0.96);
  border-bottom: 1px solid rgba(226, 232, 240, 0.86);
  backdrop-filter: blur(18px);
}

.wecom-detail-nav h1 {
  margin: 0;
  color: #060606;
  font-size: 19px;
  font-weight: 700;
  line-height: 1.2;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-detail-nav__actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
}

.wecom-nav-button {
  width: 36px;
  height: 36px;
  padding: 0;
  border: 0;
  border-radius: 0;
  display: inline-grid;
  place-items: center;
  background: transparent;
  color: #050505;
}

.wecom-nav-button ion-icon {
  width: 26px;
  height: 26px;
  stroke-width: 34px;
}

.wecom-nav-button--pdf {
  position: relative;
  width: 29px;
  height: 32px;
  border: 2px solid #080808;
  border-radius: 6px;
  font-size: 8px;
  font-weight: 800;
  line-height: 1;
}

.wecom-nav-button--pdf span {
  transform: translateY(-3px);
}

.wecom-nav-button--pdf ion-icon {
  position: absolute;
  right: -7px;
  bottom: -7px;
  width: 21px;
  height: 21px;
  padding: 1px;
  background: #f0f1f5;
}

.wecom-detail-main {
  padding: 12px 12px calc(88px + env(safe-area-inset-bottom));
}

.wecom-detail-main > section {
  border: 1px solid rgba(226, 232, 240, 0.92);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.035);
}

.wecom-detail-main > section + section {
  margin-top: 10px;
}

.wecom-applicant-section,
.wecom-detail-section,
.wecom-attachment-section,
.wecom-flow-section,
.wecom-remind-section,
.wecom-action-section,
.wecom-loading-section {
  background: #ffffff;
}

.wecom-applicant-section {
  min-height: 78px;
  padding: 16px 18px;
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
}

.wecom-avatar {
  width: 46px;
  height: 46px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.55), transparent 48%),
    #5d9cf8;
  color: #ffffff;
  font-size: 20px;
  font-weight: 700;
}

.wecom-applicant-copy {
  min-width: 0;
}

.wecom-applicant-title {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  color: #111111;
  font-size: 19px;
  font-weight: 700;
  line-height: 1.32;
}

.wecom-applicant-title > span:first-child {
  min-width: 0;
  overflow-wrap: anywhere;
}

.wecom-applicant-meta {
  margin-top: 7px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  color: #667085;
  font-size: 13px;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
}

.wecom-status-badge {
  flex: 0 0 auto;
  margin-top: 2px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.1;
}

.wecom-status-badge--success {
  background: #e7f6e8;
  color: #3caf55;
}

.wecom-status-badge--pending {
  background: #e8f1ff;
  color: #2f7ce8;
}

.wecom-status-badge--danger {
  background: #fff0ef;
  color: #e14c45;
}

.wecom-status-badge--muted {
  background: #f0f1f3;
  color: #6f7680;
}

.wecom-detail-section {
  padding: 20px 18px 22px;
}

.wecom-detail-section--extra {
  padding-top: 20px;
}

.wecom-detail-row {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  column-gap: 14px;
  align-items: start;
}

.wecom-detail-row + .wecom-detail-row {
  margin-top: 13px;
}

.wecom-detail-label {
  color: #2f3338;
  font-size: 16px;
  font-weight: 400;
  line-height: 1.45;
}

.wecom-detail-value {
  min-width: 0;
  color: #111111;
  font-size: 17px;
  font-weight: 500;
  line-height: 1.48;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}

.wecom-detail-value--multiline {
  white-space: pre-wrap;
  line-height: 1.54;
}

.wecom-detail-attachments {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.wecom-detail-attachment {
  width: 92px;
  min-width: 0;
  padding: 0;
  border: 0;
  display: grid;
  gap: 6px;
  background: transparent;
  color: #111111;
  text-align: left;
}

.wecom-detail-attachment__thumb {
  width: 72px;
  height: 72px;
  border-radius: 6px;
  overflow: hidden;
  display: grid;
  place-items: center;
  background: #f2f4f8;
}

.wecom-detail-attachment__thumb img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
}

.wecom-detail-attachment__thumb--file span {
  max-width: calc(100% - 12px);
  padding: 5px 6px;
  border-radius: 4px;
  background: #ffffff;
  color: #2f7ce8;
  font-size: 12px;
  font-weight: 800;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-detail-attachment__name {
  min-width: 0;
  color: #4b5563;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-detail-helper {
  grid-column: 2;
  margin-top: 8px;
  color: #9a9a9a;
  font-size: 13px;
  line-height: 1.45;
}

.wecom-detail-helper button {
  padding: 0;
  border: 0;
  background: transparent;
  color: #3a73c6;
  font: inherit;
}

.wecom-attachment-section {
  padding: 18px;
}

.wecom-attachment-section h2 {
  margin: 0;
  color: #111111;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
}

.wecom-attachment-grid {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  gap: 12px;
}

.wecom-attachment-card {
  min-width: 0;
  padding: 0;
  border: 1px solid #edf0f5;
  border-radius: 6px;
  overflow: hidden;
  display: grid;
  grid-template-rows: 104px auto auto;
  background: #ffffff;
  color: #111111;
  text-align: left;
}

.wecom-attachment-thumb {
  min-width: 0;
  background: #f4f6fa;
  display: grid;
  place-items: center;
}

.wecom-attachment-thumb img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
}

.wecom-attachment-thumb--file span {
  max-width: calc(100% - 24px);
  padding: 8px 10px;
  border-radius: 4px;
  background: #ffffff;
  color: #2f7ce8;
  font-size: 15px;
  font-weight: 800;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-attachment-name {
  min-width: 0;
  padding: 9px 10px 0;
  color: #1f2937;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-attachment-action {
  padding: 5px 10px 10px;
  color: #2f7ce8;
  font-size: 13px;
  font-weight: 700;
}

.wecom-flow-section {
  padding: 20px 18px 4px;
}

.wecom-flow-section h2,
.wecom-action-section h2 {
  margin: 0;
  color: #111111;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
}

.wecom-flow-list {
  margin-top: 16px;
}

.wecom-flow-step {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 10px;
}

.wecom-flow-step + .wecom-flow-step {
  margin-top: 18px;
}

.wecom-flow-rail {
  position: relative;
  display: flex;
  justify-content: center;
}

.wecom-flow-step:not(:last-child) .wecom-flow-rail::after {
  content: '';
  position: absolute;
  top: 14px;
  bottom: -22px;
  width: 1px;
  border-radius: 999px;
  background: #d9dde5;
}

.wecom-flow-icon {
  position: relative;
  z-index: 1;
  width: 8px;
  height: 8px;
  margin-top: 8px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #a8aeb8;
  color: transparent;
}

.wecom-flow-icon ion-icon {
  display: none;
}

.wecom-flow-content {
  min-width: 0;
  padding-bottom: 16px;
  border-bottom: 1px solid #eef0f4;
}

.wecom-flow-step:last-child .wecom-flow-content {
  border-bottom: 0;
}

.wecom-flow-header {
  width: 100%;
  padding: 0;
  border: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  background: transparent;
  text-align: left;
}

.wecom-flow-header strong {
  min-width: 0;
  color: #111111;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.42;
  overflow-wrap: anywhere;
}

.wecom-flow-caret {
  display: none;
}

.wecom-flow-people {
  margin-top: 10px;
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.wecom-flow-person-avatar {
  position: relative;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.45), transparent 46%),
    #91b278;
  color: #ffffff;
  font-size: 14px;
  font-weight: 700;
}

.wecom-flow-person-check {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border: 2px solid #ffffff;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #4b9df6;
}

.wecom-flow-person-check ion-icon {
  width: 12px;
  height: 12px;
}

.wecom-flow-person-name {
  min-width: 0;
  color: #111111;
  font-size: 15px;
  font-weight: 500;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.wecom-flow-person-action {
  color: #8a8a8a;
  font-size: 13px;
  line-height: 1.35;
  white-space: nowrap;
}

.wecom-flow-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  color: #667085;
  font-size: 13px;
  line-height: 1.42;
  font-variant-numeric: tabular-nums;
}

.wecom-flow-comment {
  margin-top: 10px;
  color: #777777;
  font-size: 14px;
  line-height: 1.45;
}

.wecom-empty-text,
.wecom-loading-section {
  margin: 0;
  color: #777777;
  font-size: 15px;
  line-height: 1.5;
}

.wecom-empty-text {
  margin-top: 18px;
  padding-bottom: 28px;
}

.wecom-loading-section {
  padding: 20px 18px;
}

.wecom-remind-section {
  padding: 18px;
}

.wecom-remind-button {
  margin: 0;
  --border-color: #2f7ce8;
  --color: #2f7ce8;
  font-weight: 800;
}

.wecom-action-section {
  padding: 20px 18px 24px;
}

.wecom-action-section :deep(ion-textarea) {
  margin-top: 14px;
}

.wecom-action-buttons {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.wecom-withdraw-button {
  margin-top: 14px;
}

@media (max-width: 390px) {
  .wecom-detail-nav {
    grid-template-columns: 70px minmax(0, 1fr) 70px;
    padding-left: 12px;
    padding-right: 12px;
  }

  .wecom-detail-nav h1 {
    font-size: 18px;
  }

  .wecom-applicant-section {
    padding-left: 16px;
    padding-right: 16px;
    grid-template-columns: 42px minmax(0, 1fr);
  }

  .wecom-avatar {
    width: 42px;
    height: 42px;
    font-size: 18px;
  }

  .wecom-applicant-title {
    font-size: 18px;
  }

  .wecom-detail-section,
  .wecom-attachment-section,
  .wecom-flow-section,
  .wecom-remind-section,
  .wecom-action-section,
  .wecom-loading-section {
    padding-left: 16px;
    padding-right: 16px;
  }

  .wecom-detail-row {
    grid-template-columns: 84px minmax(0, 1fr);
    column-gap: 12px;
  }

  .wecom-detail-label {
    font-size: 16px;
  }

  .wecom-detail-value {
    font-size: 17px;
  }

  .wecom-flow-section h2,
  .wecom-action-section h2 {
    font-size: 17px;
  }
}
</style>
