<template>
  <section class="approval-flow-workbench">
    <div class="approval-flow-canvas" :style="{ '--flow-scale': zoom / 100 }">
      <div class="approval-flow-scale">
        <button class="applicant-node" type="button" @click="$emit('select-applicant')">
          <span>发起人</span>
          <strong>{{ templateName || '申请人' }}</strong>
        </button>

        <FlowSequence
          :nodes="nodes"
          :selected-node-uid="selectedNodeUid"
          :member-names="memberNames"
          :condition-field-labels="conditionFieldLabels"
          :company-names="companyNames"
          :department-names="departmentNames"
          @select-node="$emit('select-node', $event)"
          @select-branch="$emit('select-branch', $event)"
          @copy-branch="$emit('copy-branch', $event)"
          @add-branch="$emit('add-branch', $event)"
          @insert-node="$emit('insert-node', $event)"
          @remove-node="$emit('remove-node', $event)"
          @remove-branch="$emit('remove-branch', $event)"
        />

        <div class="flow-end-node">流程结束</div>
        <div class="flow-automation">
          <span>审批流程结束后，自动发起下一个任务</span>
          <button type="button" @click="$emit('add-automation')">添加自动化任务</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import FlowSequence from './FlowSequence.vue'

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

withDefaults(defineProps<{
  nodes: FlowNodeEditor[]
  selectedNodeUid?: string
  templateName?: string
  zoom?: number
  memberNames?: Record<number, string>
  conditionFieldLabels?: Record<string, string>
  companyNames?: Record<number, string>
  departmentNames?: Record<number, string>
}>(), {
  selectedNodeUid: '',
  templateName: '',
  zoom: 100,
  memberNames: () => ({}),
  conditionFieldLabels: () => ({}),
  companyNames: () => ({}),
  departmentNames: () => ({}),
})

defineEmits<{
  (event: 'select-node', uid: string): void
  (event: 'select-branch', payload: { nodeUid: string; branchUid: string }): void
  (event: 'copy-branch', payload: { nodeUid: string; branchUid: string }): void
  (event: 'add-branch', payload: { nodeUid: string }): void
  (event: 'insert-node', payload: { branchUid: string | null; index: number; type: FlowNodeType }): void
  (event: 'remove-node', uid: string): void
  (event: 'remove-branch', payload: { nodeUid: string; branchUid: string }): void
  (event: 'select-applicant'): void
  (event: 'add-automation'): void
}>()
</script>

<style scoped>
.approval-flow-workbench {
  position: relative;
  min-height: calc(100vh - 178px);
  background: #f4f5f7;
  overflow: auto;
}

.approval-flow-canvas {
  min-width: 100%;
  width: max-content;
  min-height: 900px;
  display: flex;
  justify-content: center;
  padding: 42px 96px 84px;
}

.approval-flow-scale {
  transform: scale(var(--flow-scale));
  transform-origin: top center;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 760px;
}

.applicant-node {
  width: 230px;
  min-height: 78px;
  border: 1px solid #e3e8ef;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.08);
  text-align: left;
  cursor: pointer;
  padding: 0;
}

.applicant-node span {
  min-height: 26px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  background: #506b9e;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.applicant-node strong {
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

.flow-end-node {
  min-width: 86px;
  height: 28px;
  border-radius: 999px;
  background: #eceff3;
  color: #9aa3ad;
  display: flex;
  align-items: center;
  justify-content: center;
}

.flow-automation {
  display: grid;
  place-items: center;
  gap: 8px;
  margin-top: 30px;
  color: #a0a7b0;
  font-size: 12px;
}

.flow-automation button {
  min-height: 30px;
  border: 1px solid #d8dee8;
  border-radius: 4px;
  background: #fff;
  color: #273449;
  padding: 0 12px;
  cursor: pointer;
}
</style>
