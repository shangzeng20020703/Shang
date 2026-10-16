<template>
  <div class="flow-sequence" :class="{ 'flow-sequence--nested': depth > 0 }">
    <FlowAddButton :menu-key="`${sequenceKey}-0`" @add="(type) => insertAt(0, type)" />

    <template v-for="(node, index) in nodes" :key="node.uid">
      <section
        v-if="isSplitNode(node)"
        class="condition-split"
        :class="{
          active: selectedNodeUid === node.uid,
          'condition-split--parallel': isParallelNode(node),
        }"
        :style="conditionSplitStyle(node)"
      >
        <button class="condition-add-pill" type="button" @click="addBranch(node.uid)">
          {{ isParallelNode(node) ? '添加分支' : '添加条件' }}
        </button>
        <span class="condition-split-arrow" aria-hidden="true"></span>
        <div class="condition-branches">
          <article
            v-for="(branch, branchIndex) in normalizedBranches(node)"
            :key="branch.uid"
            class="condition-branch-column"
            :class="{
              active: selectedNodeUid === node.uid && node.active_branch_uid === branch.uid,
            }"
          >
            <span class="condition-branch-entry-arrow" aria-hidden="true"></span>
            <article
              class="condition-card"
              :class="{
                'condition-card--default': !isParallelNode(node) && branch.is_default_branch,
                'condition-card--parallel': isParallelNode(node),
              }"
              role="button"
              tabindex="0"
              @click.stop="selectBranch(node.uid, branch)"
              @keydown.enter.prevent="selectBranch(node.uid, branch)"
              @keydown.space.prevent="selectBranch(node.uid, branch)"
            >
              <span class="condition-card-meta">
                <b>{{ branchTitle(node, branch, branchIndex) }}</b>
                <span class="condition-card-actions">
                  <button
                    v-if="canRemoveBranch(node, branch)"
                    class="condition-card-delete"
                    type="button"
                    :title="isParallelNode(node) ? '删除分支' : '删除条件'"
                    :aria-label="isParallelNode(node) ? '删除分支' : '删除条件'"
                    @click.stop="removeBranch(node.uid, branch.uid)"
                  >
                    ×
                  </button>
                  <small>{{ isParallelNode(node) ? '并行' : `优先级${branchIndex + 1}` }}</small>
                </span>
              </span>
              <strong>{{ branchSummary(node, branch, branchIndex) }}</strong>
            </article>
            <FlowSequence
              :nodes="branch.nodes || []"
              :branch-uid="branch.uid"
              :selected-node-uid="selectedNodeUid"
              :member-names="memberNames"
              :condition-field-labels="conditionFieldLabels"
              :company-names="companyNames"
              :department-names="departmentNames"
              :depth="depth + 1"
              @select-node="$emit('select-node', $event)"
              @select-branch="$emit('select-branch', $event)"
              @copy-branch="$emit('copy-branch', $event)"
              @add-branch="$emit('add-branch', $event)"
              @insert-node="$emit('insert-node', $event)"
              @remove-node="$emit('remove-node', $event)"
              @remove-branch="$emit('remove-branch', $event)"
            />
            <span class="condition-branch-exit-arrow" aria-hidden="true"></span>
          </article>
        </div>
        <div class="condition-merge" aria-hidden="true"></div>
      </section>
      <div v-else class="flow-node-shell">
        <span class="flow-node-entry-arrow" aria-hidden="true"></span>
        <article
          class="flow-node"
          :class="[nodeClass(node.node_type), { active: selectedNodeUid === node.uid }]"
          role="button"
          tabindex="0"
          @click.stop="selectNode(node.uid)"
          @keydown.enter.prevent="selectNode(node.uid)"
          @keydown.space.prevent="selectNode(node.uid)"
        >
          <span class="flow-node-head">
            <b>{{ nodeTypeLabel(node.node_type) }}</b>
          </span>
          <span class="flow-node-body">{{ nodeSummary(node) }}</span>
          <button
            class="flow-node-delete"
            type="button"
            title="删除节点"
            aria-label="删除节点"
            @click.stop="removeNode(node.uid)"
          >
            ×
          </button>
        </article>
      </div>

      <FlowAddButton :menu-key="`${sequenceKey}-${index + 1}`" @add="(type) => insertAt(index + 1, type)" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, type Component, type CSSProperties } from 'vue'
import {
  Connection,
  CircleCloseFilled,
  DocumentChecked,
  InfoFilled,
  MagicStick,
  Money,
  Operation,
  Promotion,
  Share,
  Stamp,
} from '@element-plus/icons-vue'

defineOptions({ name: 'FlowSequence' })

type FlowNodeType = 'approval' | 'notify' | 'handler' | 'auto' | 'condition_branch' | 'parallel_branch'
type ConditionOperator = 'eq' | 'neq' | 'in' | 'not_in' | 'contains' | 'not_contains' | 'empty' | 'not_empty' | 'lt' | 'lte' | 'gt' | 'gte' | 'between'
type ApplicantScopeValue = {
  type: 'member' | 'department' | 'company' | 'role'
  id?: number
  value?: string
  label?: string
}
type FlowConditionEditor = {
  uid: string
  field: string
  operator: ConditionOperator
  value: string | number | ApplicantScopeValue | Array<string | number | ApplicantScopeValue> | null
}
type FlowConditionGroupEditor = {
  uid: string
  label: string
  condition_combinator: 'and' | 'or'
  conditions: FlowConditionEditor[]
}
type FlowBranchEditor = {
  uid: string
  label: string
  is_default_branch: boolean
  condition_combinator: 'and' | 'or'
  conditions: FlowConditionEditor[]
  condition_group_combinator?: 'and' | 'or'
  condition_groups?: FlowConditionGroupEditor[]
  nodes: FlowNodeEditor[]
}
type FlowNodeEditor = {
  uid: string
  node_type: FlowNodeType
  assignee_source: string
  approver_id: number | null
  approver_ids: number[]
  role: string
  approval_mode: 'or_sign' | 'counter_sign' | 'sequential' | 'vote'
  allow_applicant_select: boolean
  include_applicant_self: boolean
  branches?: FlowBranchEditor[]
  active_branch_uid?: string
}

const props = withDefaults(defineProps<{
  nodes: FlowNodeEditor[]
  branchUid?: string | null
  selectedNodeUid?: string
  memberNames?: Record<number, string>
  conditionFieldLabels?: Record<string, string>
  companyNames?: Record<number, string>
  departmentNames?: Record<number, string>
  depth?: number
}>(), {
  branchUid: null,
  selectedNodeUid: '',
  memberNames: () => ({}),
  conditionFieldLabels: () => ({}),
  companyNames: () => ({}),
  departmentNames: () => ({}),
  depth: 0,
})

const emit = defineEmits<{
  (event: 'select-node', uid: string): void
  (event: 'select-branch', payload: { nodeUid: string; branchUid: string }): void
  (event: 'copy-branch', payload: { nodeUid: string; branchUid: string }): void
  (event: 'add-branch', payload: { nodeUid: string }): void
  (event: 'insert-node', payload: { branchUid: string | null; index: number; type: FlowNodeType }): void
  (event: 'remove-node', uid: string): void
  (event: 'remove-branch', payload: { nodeUid: string; branchUid: string }): void
}>()

const sequenceKey = computed(() => props.branchUid || 'root')
const openAddMenuKey = ref('')
const FLOW_NODE_WIDTH = 230
const MIN_SPLIT_WIDTH = 340
const BRANCH_GAP = 96

function closeAddMenuOnOutsidePointer(event: PointerEvent) {
  if (!openAddMenuKey.value) return
  const target = event.target
  if (target instanceof Element && target.closest('.flow-add-menu, .flow-add-button')) return
  openAddMenuKey.value = ''
}

function closeAddMenuOnEscape(event: KeyboardEvent) {
  if (event.key === 'Escape') openAddMenuKey.value = ''
}

onMounted(() => {
  document.addEventListener('pointerdown', closeAddMenuOnOutsidePointer)
  document.addEventListener('keydown', closeAddMenuOnEscape)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', closeAddMenuOnOutsidePointer)
  document.removeEventListener('keydown', closeAddMenuOnEscape)
})

type AddMenuItem = {
  type?: FlowNodeType
  label: string
  icon: Component
  tone: string
  disabled?: boolean
  suffix?: string
  info?: boolean
}
const addNodeMenuSections: Array<{ title: string; items: AddMenuItem[] }> = [
  {
    title: '人工节点',
    items: [
      { type: 'approval', label: '审批人', icon: Stamp, tone: 'approval' },
      { type: 'notify', label: '抄送人', icon: Promotion, tone: 'notify' },
      { type: 'handler', label: '办理人', icon: DocumentChecked, tone: 'handler' },
    ],
  },
  {
    title: '分支节点',
    items: [
      { type: 'condition_branch', label: '条件分支', icon: Share, tone: 'condition' },
      { type: 'parallel_branch', label: '并行分支', icon: Operation, tone: 'parallel' },
    ],
  },
  {
    title: '自动化',
    items: [
      { type: 'auto', label: '自动化', icon: MagicStick, tone: 'auto' },
      { label: '连接器', icon: Connection, tone: 'connector', disabled: true, suffix: '◆' },
    ],
  },
  {
    title: '套件',
    items: [
      { label: '付款人', icon: Money, tone: 'suite', disabled: true, info: true },
    ],
  },
]

const FlowAddButton = defineComponent({
  name: 'FlowAddButton',
  props: {
    menuKey: {
      type: String,
      required: true,
    },
  },
  emits: ['add'],
  setup(addProps, { emit: addEmit }) {
    const isOpen = computed(() => openAddMenuKey.value === addProps.menuKey)
    const toggle = () => {
      openAddMenuKey.value = isOpen.value ? '' : addProps.menuKey
    }
    const select = (type?: FlowNodeType) => {
      if (!type) return
      addEmit('add', type)
      openAddMenuKey.value = ''
    }
    return () => h('div', { class: 'flow-add-point' }, [
      h('button', {
        class: 'flow-add-button',
        type: 'button',
        'aria-label': '添加流程节点',
        onClick: toggle,
      }, '+'),
      isOpen.value ? h('div', { class: 'flow-add-menu' }, addNodeMenuSections.map((section) => h('section', {
        class: 'flow-add-section',
      }, [
        h('h4', section.title),
        h('div', { class: 'flow-add-grid' }, section.items.map((item) => h('button', {
          type: 'button',
          class: ['flow-add-option', `flow-add-option--${item.tone}`, { disabled: item.disabled }],
          disabled: item.disabled,
          title: item.disabled ? '该节点尚未接入后端运行规则，暂不可选' : `添加${item.label}`,
          onClick: () => select(item.type),
        }, [
          h('span', { class: 'flow-add-icon' }, [h(item.icon, { class: 'flow-add-icon-svg' })]),
          h('b', [
            item.label,
            item.info ? h(InfoFilled, { class: 'flow-add-info-icon' }) : null,
            item.disabled ? h(CircleCloseFilled, { class: 'flow-add-disabled-icon' }) : null,
          ]),
          item.suffix ? h('small', { class: 'flow-add-suffix' }, item.suffix) : null,
        ]))),
      ]))) : null,
    ])
  },
})

function insertAt(index: number, type: FlowNodeType) {
  emit('insert-node', {
    branchUid: props.branchUid || null,
    index,
    type,
  })
}

function selectNode(uid: string) {
  emit('select-node', uid)
}

function removeNode(uid: string) {
  emit('remove-node', uid)
}

function selectBranch(nodeUid: string, branch: FlowBranchEditor) {
  emit('select-branch', { nodeUid, branchUid: branch.uid })
}

function copyBranch(nodeUid: string, branchUid: string) {
  emit('copy-branch', { nodeUid, branchUid })
}

function removeBranch(nodeUid: string, branchUid: string) {
  emit('remove-branch', { nodeUid, branchUid })
}

function addBranch(nodeUid: string) {
  emit('add-branch', { nodeUid })
}

type ConditionSplitMetrics = {
  columnWidth: number
  trackWidth: number
  splitWidth: number
  firstBranchHalf: number
  lastBranchHalf: number
}

function normalizedBranches(node: FlowNodeEditor): FlowBranchEditor[] {
  return node.branches?.length ? node.branches : []
}

function isParallelNode(node: FlowNodeEditor) {
  return node.node_type === 'parallel_branch'
}

function isSplitNode(node: FlowNodeEditor) {
  return node.node_type === 'condition_branch' || node.node_type === 'parallel_branch'
}

function sequenceVisualWidth(nodes: FlowNodeEditor[] = []): number {
  return nodes.reduce((width, node) => {
    if (!isSplitNode(node)) return Math.max(width, FLOW_NODE_WIDTH)
    return Math.max(width, conditionSplitMetrics(node).splitWidth)
  }, FLOW_NODE_WIDTH)
}

function branchVisualWidth(branch: FlowBranchEditor): number {
  return Math.max(FLOW_NODE_WIDTH, sequenceVisualWidth(branch.nodes || []))
}

function conditionSplitMetrics(node: FlowNodeEditor): ConditionSplitMetrics {
  const branches = normalizedBranches(node)
  const branchWidths: number[] = branches.length ? branches.map((branch) => branchVisualWidth(branch)) : [FLOW_NODE_WIDTH]
  const columnWidth = Math.max(...branchWidths)
  const trackWidth: number = branchWidths.length * columnWidth + Math.max(branchWidths.length - 1, 0) * BRANCH_GAP
  return {
    columnWidth,
    trackWidth,
    splitWidth: Math.max(MIN_SPLIT_WIDTH, trackWidth),
    firstBranchHalf: columnWidth / 2,
    lastBranchHalf: columnWidth / 2,
  }
}

function conditionSplitStyle(node: FlowNodeEditor): CSSProperties {
  const metrics = conditionSplitMetrics(node)
  return {
    '--branch-count': String(Math.max(node.branches?.length || 1, 1)),
    '--branch-track-width': `${metrics.trackWidth}px`,
    '--branch-column-width': `${metrics.columnWidth}px`,
    '--split-width': `${metrics.splitWidth}px`,
    '--first-branch-half': `${metrics.firstBranchHalf}px`,
    '--last-branch-half': `${metrics.lastBranchHalf}px`,
  } as CSSProperties
}

function nodeTypeLabel(type: FlowNodeType) {
  if (type === 'approval') return '审批人'
  if (type === 'notify') return '抄送人'
  if (type === 'handler') return '办理人'
  if (type === 'condition_branch') return '条件分支'
  if (type === 'parallel_branch') return '并行分支'
  return '自动处理'
}

function nodeClass(type: FlowNodeType) {
  if (type === 'approval') return 'flow-node--approval'
  if (type === 'notify') return 'flow-node--notify'
  if (type === 'handler') return 'flow-node--handler'
  if (type === 'auto') return 'flow-node--auto'
  if (type === 'parallel_branch') return 'flow-node--parallel'
  return 'flow-node--condition'
}

function memberName(id: number | null | undefined) {
  if (!id) return ''
  return props.memberNames[id] || `员工#${id}`
}

function nodeMemberIds(node: FlowNodeEditor) {
  const ids = Array.isArray(node.approver_ids) ? node.approver_ids : []
  if (ids.length) return ids
  return node.approver_id ? [node.approver_id] : []
}

function nodeSummary(node: FlowNodeEditor) {
  if (node.node_type === 'parallel_branch') return `${node.branches?.length || 0} 条分支同时发起`
  if (node.node_type === 'auto') return '系统自动处理'
  if (node.assignee_source === 'specific_user') {
    const names = nodeMemberIds(node).map((id) => memberName(id)).filter(Boolean)
    return names.length ? names.join('、') : node.node_type === 'handler' ? '请选择办理人' : node.node_type === 'notify' ? '请选择抄送人' : '请选择审批人'
  }
  if (node.assignee_source === 'role') return `${node.role || '指定角色'}`
  if (node.assignee_source === 'department_head') return '部门主管'
  if (node.assignee_source === 'multi_level_manager') return '连续多级主管'
  if (node.assignee_source === 'applicant_self') return '发起人自己'
  if (node.assignee_source === 'applicant_select') return '发起人自选'
  if (node.assignee_source === 'related_member_field') return '表单内的联系人'
  if (node.assignee_source === 'form_department_head') return '表单内部门主管'
  return node.node_type === 'notify' && node.include_applicant_self ? '发起人本人' : '直属上级'
}

function branchTitle(node: FlowNodeEditor, branch: FlowBranchEditor, branchIndex: number) {
  if (isParallelNode(node)) return branch.label || `分支${branchIndex + 1}`
  return branch.is_default_branch ? '默认条件' : branch.label || `条件${branchIndex + 1}`
}

function canRemoveBranch(node: FlowNodeEditor, branch: FlowBranchEditor) {
  if (isParallelNode(node)) return (node.branches?.length || 0) > 2
  return !branch.is_default_branch
}

function conditionOperatorLabel(operator: ConditionOperator) {
  const labels: Record<ConditionOperator, string> = {
    eq: '等于',
    neq: '不等于',
    in: '属于',
    not_in: '不属于',
    contains: '包含',
    not_contains: '不包含',
    empty: '为空',
    not_empty: '不为空',
    lt: '小于',
    lte: '小于等于',
    gt: '大于',
    gte: '大于等于',
    between: '介于',
  }
  return labels[operator] || operator
}

function conditionValueLabel(condition: FlowConditionEditor) {
  if (condition.operator === 'empty' || condition.operator === 'not_empty') return ''
  const value = condition.value
  const field = String(condition.field || '')
  const rawValues = Array.isArray(value) ? value : [value]
  const labels = rawValues
    .filter((item) => item !== null && item !== undefined && item !== '')
    .map((item) => {
      if (typeof item === 'object' && item && 'type' in item) {
        if (item.label) return item.label
        const numericId = Number(item.id)
        if (item.type === 'member' && Number.isFinite(numericId)) return memberName(numericId)
        if (item.type === 'department' && Number.isFinite(numericId)) return props.departmentNames[numericId] || String(item.id)
        if (item.type === 'company' && Number.isFinite(numericId)) return props.companyNames[numericId] || String(item.id)
        return item.value || '角色'
      }
      const numericId = Number(item)
      if (field.includes('company') && Number.isFinite(numericId)) return props.companyNames[numericId] || String(item)
      if (field.includes('department') && Number.isFinite(numericId)) return props.departmentNames[numericId] || String(item)
      return String(item)
    })
  if (condition.operator === 'between' && labels.length >= 2) return `${labels[0]} 至 ${labels[1]}`
  return labels.join('、')
}

function branchConditionGroups(branch: FlowBranchEditor): FlowConditionGroupEditor[] {
  if (branch.condition_groups?.length) return branch.condition_groups
  if (branch.conditions?.length) {
    return [{
      uid: `${branch.uid}-legacy-group`,
      label: '条件组',
      condition_combinator: branch.condition_combinator || 'and',
      conditions: branch.conditions,
    }]
  }
  return []
}

function groupSummary(group: FlowConditionGroupEditor) {
  const conditions = group.conditions || []
  if (!conditions.length) return '请设置条件'
  const connector = group.condition_combinator === 'or' ? ' 或 ' : ' 且 '
  return conditions.map((condition) => {
    const fieldLabel = props.conditionFieldLabels[condition.field] || condition.field || '字段'
    const valueLabel = conditionValueLabel(condition)
    return `${fieldLabel}${conditionOperatorLabel(condition.operator)}${valueLabel}`
  }).join(connector)
}

function branchSummary(node: FlowNodeEditor, branch: FlowBranchEditor, _index: number) {
  if (isParallelNode(node)) {
    const childCount = branch.nodes?.length || 0
    return childCount ? `${childCount} 个节点会同时发起` : '请添加并行节点'
  }
  if (branch.is_default_branch) return '其他条件进入此流程'
  const groups = branchConditionGroups(branch)
  if (!groups.length) return '请设置条件'
  const connector = branch.condition_group_combinator === 'and' ? ' 且 ' : ' 或 '
  return groups.map((group) => groups.length > 1 ? `(${groupSummary(group)})` : groupSummary(group)).join(connector)
}
</script>

<style scoped>
.flow-sequence {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 230px;
  --flow-connector-width: 2px;
  --flow-connector-color: #1683ff;
  --flow-connector-active: #1683ff;
  --flow-arrow-width: 10px;
  --flow-arrow-height: 8px;
}

.flow-sequence--nested {
  padding-top: 0;
}

.flow-sequence :deep(.flow-add-point) {
  position: relative;
  width: 100%;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.flow-sequence :deep(.flow-add-point)::before,
.flow-sequence :deep(.flow-add-point)::after {
  content: '';
  position: absolute;
  left: 50%;
  width: var(--flow-connector-width);
  height: calc(50% + 1px);
  background: var(--flow-connector-color);
  border-radius: 0;
  transform: translateX(-50%);
}

.flow-sequence :deep(.flow-add-point)::before {
  top: -1px;
}

.flow-sequence :deep(.flow-add-point)::after {
  bottom: -1px;
}

.flow-sequence :deep(.flow-add-button) {
  position: relative;
  z-index: 4;
  width: 38px;
  height: 38px;
  border: 1px solid #d9e6f7;
  border-radius: 50%;
  background: #fff;
  color: #1683ff;
  font-size: 28px;
  font-weight: 500;
  line-height: 1;
  box-shadow: 0 3px 10px rgba(15, 23, 42, 0.08);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.flow-sequence :deep(.flow-add-button:hover),
.flow-sequence :deep(.flow-add-button:focus-visible) {
  border-color: #1683ff;
  box-shadow: 0 4px 12px rgba(22, 131, 255, 0.18);
  outline: none;
}

.flow-sequence :deep(.flow-add-menu) {
  position: absolute;
  top: -28px;
  left: calc(50% + 24px);
  z-index: 60;
  width: 520px;
  padding: 22px 24px 26px;
  border: 1px solid #d9dde4;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.08);
}

.flow-sequence :deep(.flow-add-section + .flow-add-section) {
  margin-top: 22px;
}

.flow-sequence :deep(.flow-add-section h4) {
  margin: 0 0 12px;
  color: #c5c8ce;
  font-size: 16px;
  font-weight: 700;
  line-height: 1;
}

.flow-sequence :deep(.flow-add-grid) {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.flow-sequence :deep(.flow-add-option) {
  width: 100%;
  min-height: 58px;
  border: 0;
  border-radius: 4px;
  background: #fbfcfd;
  color: #1f2530;
  text-align: left;
  padding: 0 16px 0 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 14px;
}

.flow-sequence :deep(.flow-add-option:hover:not(:disabled)) {
  background: #f5f9ff;
}

.flow-sequence :deep(.flow-add-option:disabled) {
  color: #1f2530;
  cursor: not-allowed;
}

.flow-sequence :deep(.flow-add-option b) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0;
  white-space: nowrap;
}

.flow-sequence :deep(.flow-add-suffix) {
  margin-left: -8px;
  color: #3a84ff;
  font-size: 14px;
  line-height: 1;
}

.flow-sequence :deep(.flow-add-info-icon) {
  width: 14px;
  height: 14px;
  color: #111827;
}

.flow-sequence :deep(.flow-add-disabled-icon) {
  width: 14px;
  height: 14px;
  color: #a7aeb8;
}

.flow-sequence :deep(.flow-add-icon) {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.flow-sequence :deep(.flow-add-icon-svg) {
  width: 20px;
  height: 20px;
}

.flow-sequence :deep(.flow-add-option--approval .flow-add-icon) { background: #ffa141; }
.flow-sequence :deep(.flow-add-option--notify .flow-add-icon) { background: #489cff; }
.flow-sequence :deep(.flow-add-option--handler .flow-add-icon) { background: #ff6a61; }
.flow-sequence :deep(.flow-add-option--condition .flow-add-icon) { background: #45cd99; }
.flow-sequence :deep(.flow-add-option--parallel .flow-add-icon) { background: #27b8c1; }
.flow-sequence :deep(.flow-add-option--auto .flow-add-icon) { background: #e366b2; }
.flow-sequence :deep(.flow-add-option--connector .flow-add-icon) { background: #435fff; }
.flow-sequence :deep(.flow-add-option--suite .flow-add-icon) {
  background: #fff;
  color: #b45bd6;
  border: 1px solid #e7eaf0;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06), inset 0 0 0 1px rgba(180, 91, 214, 0.04);
}

.flow-sequence :deep(.flow-add-option--suite .flow-add-icon-svg) {
  width: 20px;
  height: 20px;
}

.flow-node-shell {
  position: relative;
  width: 230px;
  display: flex;
  justify-content: center;
}

.flow-node-entry-arrow {
  position: absolute;
  left: 50%;
  top: -8px;
  z-index: 3;
  width: var(--flow-arrow-width);
  height: var(--flow-arrow-height);
  background: var(--flow-connector-color);
  clip-path: polygon(0 0, 100% 0, 50% 100%);
  transform: translateX(-50%);
  pointer-events: none;
}

.flow-node {
  position: relative;
  width: 230px;
  min-height: 78px;
  border: 1px solid #e3e8ef;
  border-radius: 6px;
  padding: 0;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.08);
  text-align: left;
  cursor: pointer;
}

.flow-node:focus-visible {
  outline: 2px solid #1677ff;
  outline-offset: 3px;
}

.flow-node.active {
  border-color: #1677ff;
  box-shadow: 0 0 0 1px #1677ff, 0 6px 14px rgba(15, 23, 42, 0.08);
}

.flow-node-delete {
  position: absolute;
  top: 2px;
  right: 8px;
  z-index: 2;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #fff;
  font-size: 26px;
  line-height: 20px;
  font-weight: 300;
  cursor: pointer;
  opacity: 0;
}

.flow-node:hover .flow-node-delete,
.flow-node.active .flow-node-delete,
.flow-node-delete:focus-visible {
  opacity: 1;
}

.flow-node-delete:hover,
.flow-node-delete:focus-visible {
  background: rgba(255, 255, 255, 0.18);
  outline: none;
}

.flow-node-head {
  min-height: 26px;
  padding: 0 42px 0 14px;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  font-size: 13px;
}

.flow-node-body {
  min-height: 52px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  color: #1f2937;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-node--approval .flow-node-head {
  background: #ff9f38;
}

.flow-node--notify .flow-node-head {
  background: #1f8fff;
}

.flow-node--handler .flow-node-head {
  background: #ff5b32;
}

.flow-node--auto .flow-node-head {
  background: #13b981;
}

.flow-node--parallel .flow-node-head {
  background: #16a3a8;
}

.condition-split {
  position: relative;
  min-width: var(--split-width, 340px);
  padding: 0;
  overflow: visible;
}

.condition-split::before {
  content: '';
  position: absolute;
  top: 32px;
  left: 50%;
  width: var(--flow-connector-width);
  height: 33px;
  background: var(--flow-connector-color);
  border-radius: 0;
  transform: translateX(-50%);
}

.condition-split.active > .condition-add-pill {
  border-color: #1677ff;
  color: #1677ff;
  box-shadow: 0 4px 12px rgba(22, 131, 255, 0.16);
}

.condition-split.active::before,
.condition-split.active > .condition-branches::before,
.condition-split.active > .condition-merge::before,
.condition-split.active > .condition-merge::after {
  background: var(--flow-connector-active);
}

.condition-add-pill {
  position: relative;
  z-index: 5;
  display: block;
  min-width: 82px;
  height: 32px;
  margin: 0 auto 32px;
  border: 1px solid #d9e6f7;
  border-radius: 999px;
  background: #fff;
  color: #1677ff;
  font-size: 13px;
  cursor: pointer;
  box-shadow: 0 3px 10px rgba(15, 23, 42, 0.08);
}

.condition-add-pill:hover,
.condition-add-pill:focus-visible {
  border-color: #1683ff;
  box-shadow: 0 4px 12px rgba(22, 131, 255, 0.18);
  outline: none;
}

.condition-split-arrow,
.condition-branch-entry-arrow,
.condition-branch-exit-arrow {
  position: absolute;
  left: 50%;
  z-index: 3;
  width: var(--flow-arrow-width);
  height: var(--flow-arrow-height);
  border: 0;
  background: var(--flow-connector-color);
  clip-path: polygon(0 0, 100% 0, 50% 100%);
  transform: translateX(-50%);
  pointer-events: none;
}

.condition-split.active > .condition-split-arrow,
.condition-branch-column.active > .condition-branch-entry-arrow,
.condition-branch-column.active > .condition-branch-exit-arrow {
  background: var(--flow-connector-active);
}

.condition-split-arrow {
  top: 56px;
}

.condition-branches {
  position: relative;
  display: flex;
  justify-content: center;
  gap: 96px;
  align-items: stretch;
  width: var(--branch-track-width, 230px);
  margin: 0 auto;
  padding: 46px 0 0;
}

.condition-branches::before {
  content: '';
  position: absolute;
  top: 0;
  left: var(--first-branch-half, 115px);
  right: var(--last-branch-half, 115px);
  height: var(--flow-connector-width);
  background: var(--flow-connector-color);
  border-radius: 0;
}

.condition-branch-column {
  position: relative;
  width: var(--branch-column-width, 230px);
  min-width: var(--branch-column-width, 230px);
  display: flex;
  flex-direction: column;
  align-items: center;
}

.condition-branch-column::before {
  content: '';
  position: absolute;
  top: -46px;
  left: 50%;
  z-index: 0;
  width: var(--flow-connector-width);
  height: 47px;
  background: var(--flow-connector-color);
  border-radius: 0;
  transform: translateX(-50%);
}

.condition-branch-column::after {
  content: '';
  position: absolute;
  top: 92px;
  bottom: -54px;
  left: 50%;
  z-index: 0;
  width: var(--flow-connector-width);
  height: auto;
  background: var(--flow-connector-color);
  border-radius: 0;
  transform: translateX(-50%);
}

.condition-branch-column.active::before,
.condition-branch-column.active::after {
  background: var(--flow-connector-active);
}

.condition-card,
.condition-branch-column > .flow-sequence--nested {
  position: relative;
  z-index: 2;
}

.condition-branch-entry-arrow {
  top: -8px;
}

.condition-branch-exit-arrow {
  bottom: -54px;
}

.condition-card {
  width: 230px;
  min-height: 92px;
  border: 1px solid #e3e8ef;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.08);
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  outline: none;
}

.condition-branch-column.active > .condition-card {
  border-color: #1677ff;
  box-shadow: 0 0 0 1px #1677ff, 0 6px 14px rgba(15, 23, 42, 0.08);
}

.condition-card--default {
  cursor: pointer;
}

.condition-card--parallel .condition-card-meta b {
  color: #168c92;
}

.condition-card--parallel .condition-card-meta small {
  color: #168c92;
}

.condition-card-meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 18px;
}

.condition-card-meta b {
  color: #00b578;
  font-size: 14px;
  line-height: 1.3;
}

.condition-card-meta small {
  color: #8b95a1;
  font-size: 13px;
  white-space: nowrap;
}

.condition-card-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.condition-card-delete {
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #94a3b8;
  font-size: 24px;
  line-height: 20px;
  font-weight: 300;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
}

.condition-card:hover .condition-card-delete,
.condition-branch-column.active > .condition-card .condition-card-delete,
.condition-card-delete:focus-visible {
  opacity: 1;
}

.condition-card-delete:hover,
.condition-card-delete:focus-visible {
  background: #f1f5f9;
  color: #ef4444;
  outline: none;
}

.condition-card strong {
  display: block;
  color: #1f2937;
  font-size: 14px;
  line-height: 1.55;
  font-weight: 600;
  border-bottom: 1px solid #edf0f4;
  padding-bottom: 12px;
}

.condition-card--default .condition-card-meta b,
.condition-card--default .condition-card-meta small,
.condition-card--default strong {
  color: #9aa3ad;
}

.condition-merge {
  position: relative;
  width: var(--branch-track-width, 230px);
  height: 78px;
  margin: 54px auto 0;
}

.condition-merge::before {
  content: '';
  position: absolute;
  top: 0;
  left: var(--first-branch-half, 115px);
  right: var(--last-branch-half, 115px);
  height: var(--flow-connector-width);
  background: var(--flow-connector-color);
  border-radius: 0;
}

.condition-merge::after {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: var(--flow-connector-width);
  height: 100%;
  background: var(--flow-connector-color);
  border-radius: 0;
  transform: translateX(-50%);
}

</style>
