/**
 * 状态映射工具 - Status Mapping Utilities
 *
 * 后端 API 返回英文状态值，前端需要显示中文。
 * 本文件提供统一的映射函数，供各 View 页面使用。
 */

// ============ 通用状态映射 ============

/** 请假审批状态 (leave requests) */
const leaveStatusMap: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  cancelled: '已撤回',
}

/** 审批流程实例状态 (approval instances) */
const approvalStatusMap: Record<string, string> = {
  pending: '审批中',
  approved: '已通过',
  rejected: '已驳回',
  cancelled: '已取消',
  withdrawn: '已撤回',
}

/** 公告状态 */
const announcementStatusMap: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  expired: '已过期',
}

/** 公告分类 */
const announcementCategoryMap: Record<string, string> = {
  notice: '公司动态',
  policy: '制度通知',
  activity: '活动公告',
  hr: '人事通知',
  other: '其他',
}

/** 资产状态 */
const assetStatusMap: Record<string, string> = {
  available: '空闲',
  checked_out: '在用',
  maintenance: '维修中',
  retired: '已报废',
}

/** 访客状态 */
const visitorStatusMap: Record<string, string> = {
  pending: '已预约',
  approved: '已确认',
  checked_in: '已签到',
  checked_out: '已签退',
  cancelled: '已取消',
}

/** 会议室预约状态 */
const meetingStatusMap: Record<string, string> = {
  confirmed: '已预约',
  cancelled: '已取消',
  ongoing: '进行中',
  ended: '已结束',
}

/** 用车申请状态 */
const vehicleStatusMap: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  in_use: '使用中',
  returned: '已归还',
}

/** 供应商状态 */
const vendorStatusMap: Record<string, string> = {
  active: '合作中',
  suspended: '已暂停',
  terminated: '已终止',
}

/** 供应商类型 */
const vendorTypeMap: Record<string, string> = {
  outsource: '外包服务',
  headhunter: '猎头',
  service: '服务',
  training: '培训机构',
  labor_dispatch: '劳务派遣',
}

/** 供应商合同状态 */
const vendorContractStatusMap: Record<string, string> = {
  active: '生效中',
  expired: '已到期',
  terminated: '已终止',
}

/** 供应商合同类型 */
const vendorContractTypeMap: Record<string, string> = {
  outsource_dispatch: '劳务派遣',
  headhunter_service: '猎头协议',
  service_agreement: '服务协议',
  training: '培训合同',
  other: '其他',
}

/** 猎头佣金支付状态 */
const commissionPaymentStatusMap: Record<string, string> = {
  pending: '待确认',
  confirmed: '已确认',
  paid: '已支付',
  cancelled: '已取消',
}

/** 绩效考核周期状态 */
const performanceCycleStatusMap: Record<string, string> = {
  not_started: '未开始',
  self_eval: '自评中',
  superior_eval: '上级评分',
  completed: '已完成',
}

/** 绩效考核周期类型 */
const performanceCycleTypeMap: Record<string, string> = {
  quarterly: '季度考核',
  semi_annual: '半年度考核',
  annual: '年度考核',
}

/** 绩效评估记录状态 */
const performanceEvalStatusMap: Record<string, string> = {
  pending_self: '待自评',
  self_completed: '自评完成',
  pending_superior: '待上级评',
  completed: '已完成',
}

/** 绩效模板状态 */
const performanceTemplateStatusMap: Record<string, string> = {
  active: '启用',
  inactive: '停用',
  enabled: '启用',
  disabled: '停用',
}

/** 薪酬状态 */
const payrollStatusMap: Record<string, string> = {
  draft: '草稿',
  pending: '待审批',
  approved: '已审批',
  paid: '已发放',
}

/** 资产领用状态 */
const assetCheckoutStatusMap: Record<string, string> = {
  active: '在用',
  returned: '已归还',
  overdue: '逾期',
}

/** 考勤申诉状态 */
const correctionStatusMap: Record<string, string> = {
  none: '无',
  pending: '待审批',
  approved: '已通过',
  rejected: '已驳回',
}

/** 月度汇总确认状态 */
const monthlySummaryStatusMap: Record<string, string> = {
  draft: '草稿',
  employee_confirmed: '员工已确认',
  locked: '已锁定',
}

/** 员工合同状态 */
const contractStatusMap: Record<string, string> = {
  active: '生效中',
  '生效中': '生效中',
  expired: '已到期',
  '已到期': '已到期',
  terminated: '已解除',
  '已解除': '已解除',
  pending: '待生效',
  '待生效': '待生效',
}

/** 员工合同类型 */
const contractTypeMap: Record<string, string> = {
  fixed_term: '固定期限',
  '固定期限': '固定期限',
  open_ended: '无固定期限',
  unfixed_term: '无固定期限',
  '无固定期限': '无固定期限',
  intern: '实习',
  '实习': '实习',
}

/** 考勤审批流状态 */
const approvalFlowStatusMap: Record<string, string> = {
  enabled: '启用',
  disabled: '停用',
  active: '启用',
  inactive: '停用',
}

// ============ 通用翻译函数 ============

/**
 * 创建通用状态翻译函数
 * @param map 映射表
 * @returns 翻译函数
 */
function createTranslator(map: Record<string, string>) {
  return (value: string): string => {
    if (!value) return '-'
    if (map[value]) return map[value]

    const normalized = String(value).split('.').pop()?.trim().toLowerCase() || ''
    if (normalized && map[normalized]) return map[normalized]

    return value
  }
}

// ============ 导出翻译函数 ============

export const formatLeaveStatus = createTranslator(leaveStatusMap)
export const formatApprovalStatus = createTranslator(approvalStatusMap)
export const formatAnnouncementStatus = createTranslator(announcementStatusMap)
export const formatAnnouncementCategory = createTranslator(announcementCategoryMap)
export const formatAssetStatus = createTranslator(assetStatusMap)
export const formatVisitorStatus = createTranslator(visitorStatusMap)
export const formatMeetingStatus = createTranslator(meetingStatusMap)
export const formatVehicleStatus = createTranslator(vehicleStatusMap)
export const formatVendorStatus = createTranslator(vendorStatusMap)
export const formatVendorType = createTranslator(vendorTypeMap)
export const formatVendorContractStatus = createTranslator(vendorContractStatusMap)
export const formatVendorContractType = createTranslator(vendorContractTypeMap)
export const formatCommissionPaymentStatus = createTranslator(commissionPaymentStatusMap)
export const formatPerformanceCycleStatus = createTranslator(performanceCycleStatusMap)
export const formatPerformanceCycleType = createTranslator(performanceCycleTypeMap)
export const formatPerformanceEvalStatus = createTranslator(performanceEvalStatusMap)
export const formatPerformanceTemplateStatus = createTranslator(performanceTemplateStatusMap)
export const formatPayrollStatus = createTranslator(payrollStatusMap)
export const formatAssetCheckoutStatus = createTranslator(assetCheckoutStatusMap)
export const formatCorrectionStatus = createTranslator(correctionStatusMap)
export const formatMonthlySummaryStatus = createTranslator(monthlySummaryStatusMap)
export const formatContractStatus = createTranslator(contractStatusMap)
export const formatContractType = createTranslator(contractTypeMap)
export const formatApprovalFlowStatus = createTranslator(approvalFlowStatusMap)

// ============ 导出映射表（供 tag type 判断使用） ============

export {
  leaveStatusMap,
  approvalStatusMap,
  announcementStatusMap,
  announcementCategoryMap,
  assetStatusMap,
  visitorStatusMap,
  meetingStatusMap,
  vehicleStatusMap,
  vendorStatusMap,
  vendorTypeMap,
  vendorContractStatusMap,
  vendorContractTypeMap,
  commissionPaymentStatusMap,
  performanceCycleStatusMap,
  performanceCycleTypeMap,
  performanceEvalStatusMap,
  performanceTemplateStatusMap,
  payrollStatusMap,
  contractStatusMap,
  contractTypeMap,
  approvalFlowStatusMap,
}
