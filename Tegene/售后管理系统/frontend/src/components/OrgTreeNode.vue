<template>
  <div class="otn-wrap">
    <!-- Node card -->
    <div :class="['otn-card', cardClass, { 'otn-card--root': isRoot }]" @click="$emit('toggle', nodeKey)">
      <div class="otn-header">
        <span class="otn-name">{{ node.name }}</span>
        <span v-if="hasKids" :class="['otn-arrow', { 'otn-arrow--down': !isCollapsed }]">
          <svg width="10" height="10" viewBox="0 0 10 10"><polyline points="2,3 5,7 8,3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </span>
      </div>
      <div class="otn-body">
        <div class="otn-info" v-if="node.node_type === 'department'">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="#94A3B8"><path d="M8 8a3 3 0 100-6 3 3 0 000 6zm0 2c-3.31 0-6 1.34-6 3v1h12v-1c0-1.66-2.69-3-6-3z"/></svg>
          <span>{{ node.manager_name || '—' }}</span>
        </div>
        <div v-if="node.node_type === 'department'" class="otn-info otn-info--clickable" @click.stop="$emit('viewMembers', node.id)" title="点击查看成员">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="#94A3B8"><path d="M5.5 7a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm5 0a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM5.5 8.5C3.01 8.5 0 9.75 0 11v1h11v-1c0-1.25-3.01-2.5-5.5-2.5zm5 0c-.28 0-.59.02-.91.05C10.82 9.37 12 10.15 12 11v1h4v-1c0-1.25-3.01-2.5-5.5-2.5z"/></svg>
          <span class="otn-emp-count">{{ node.employee_count ?? 0 }} 人</span>
        </div>
        <div class="otn-info" v-if="node.node_type === 'department' && locName">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="#94A3B8"><path d="M8 0C5.24 0 3 2.24 3 5c0 4.13 5 9.88 5 9.88S13 9.13 13 5c0-2.76-2.24-5-5-5zm0 7.25a2.25 2.25 0 110-4.5 2.25 2.25 0 010 4.5z"/></svg>
          <span>{{ locName }}</span>
        </div>
        <div v-if="node.node_type === 'company'" class="otn-info">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="#94A3B8"><path d="M1 14h14v1H1v-1zm1-2h2V4H2v8zm4 0h2V1H6v11zm4 0h2V6h-2v6z"/></svg>
          <span>公司节点</span>
        </div>
      </div>
      <!-- Hover actions -->
      <div class="otn-actions" @click.stop>
        <button class="otn-act" :title="node.node_type === 'company' ? '查看公司信息' : '编辑'" @click="$emit('edit', node)">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="#fff"><path d="M12.15 1.85a1.5 1.5 0 012.12 2.12L5.4 12.84l-3.22.54.54-3.22z"/></svg>
        </button>
        <button class="otn-act" :title="node.node_type === 'company' ? '添加部门' : '添加子部门'" @click="$emit('add', node)">
          <svg width="12" height="12" viewBox="0 0 16 16"><line x1="8" y1="3" x2="8" y2="13" stroke="#fff" stroke-width="2" stroke-linecap="round"/><line x1="3" y1="8" x2="13" y2="8" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
        <button v-if="node.node_type !== 'company'" class="otn-act otn-act--del" title="删除" @click="$emit('remove', node)">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="#fff"><path d="M5.5 1l-.5.5h-3v1h12v-1h-3l-.5-.5h-5zM3 4v9.5A1.5 1.5 0 004.5 15h7a1.5 1.5 0 001.5-1.5V4H3z"/></svg>
        </button>
      </div>
    </div>

    <!-- Collapsed badge -->
    <div v-if="hasKids && isCollapsed" class="otn-badge" @click="$emit('toggle', nodeKey)">
      {{ childCount }} 个子节点
    </div>

    <!-- Children with connector lines -->
    <div v-if="hasKids && !isCollapsed" class="otn-children">
      <!-- Vertical line from parent down -->
      <div class="otn-vline"></div>
      <!-- Horizontal rail + children row -->
      <div class="otn-row">
        <div
          v-for="(child, idx) in node.children"
          :key="`${child.node_type}-${child.id}`"
          class="otn-child"
          :class="{
            'otn-child--first': idx === 0,
            'otn-child--last': idx === node.children.length - 1,
            'otn-child--only': node.children.length === 1,
          }"
        >
          <!-- Recursive child node -->
          <org-tree-node
            :node="child"
            :is-root="false"
            :depth="depth + 1"
            :collapsed="collapsed"
            @toggle="(id: string) => $emit('toggle', id)"
            @edit="(d: any) => $emit('edit', d)"
            @add="(d: any) => $emit('add', d)"
            @remove="(n: any) => $emit('remove', n)"
            @view-members="(id: number) => $emit('viewMembers', id)"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Dept {
  id: number
  node_type: 'company' | 'department'
  name: string
  code?: string
  parent_id?: number | null
  company_id?: number | null
  location_id?: number | null
  location?: { name: string } | null
  manager_name?: string
  employee_count?: number
  children: Dept[]
}

const props = defineProps<{
  node: Dept
  isRoot: boolean
  depth?: number
  collapsed: Set<string>
}>()

defineEmits<{
  toggle: [id: string]
  edit: [dept: any]
  add: [node: any]
  remove: [node: any]
  viewMembers: [deptId: number]
}>()

const hasKids = computed(() => props.node.children && props.node.children.length > 0)
const nodeKey = computed(() => `${props.node.node_type}-${props.node.id}`)
const isCollapsed = computed(() => props.collapsed.has(nodeKey.value))
const locName = computed(() => props.node.location?.name || null)
const depth = computed(() => props.depth ?? 0)
const cardClass = computed(() => `otn-card--lv-${Math.min(depth.value, 4)}`)

function countAll(n: Dept): number {
  let c = n.children?.length || 0
  for (const ch of n.children || []) c += countAll(ch)
  return c
}
const childCount = computed(() => countAll(props.node))
</script>

<script lang="ts">
export default { name: 'OrgTreeNode' }
</script>

<style>
/* ===== Node wrapper ===== */
.otn-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
}

/* ===== Card ===== */
.otn-card {
  width: 260px;
  min-height: 116px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 10px 26px rgba(15,23,42,0.08), 0 2px 8px rgba(15,23,42,0.05);
  border: 1px solid #D9E2EC;
  overflow: hidden;
  cursor: pointer;
  transition: box-shadow 0.2s, transform 0.2s, border-color 0.2s;
  position: relative;
}
.otn-card:hover {
  border-color: #7DD3C7;
  box-shadow: 0 16px 30px rgba(15,23,42,0.12), 0 4px 14px rgba(20,184,166,0.18);
  transform: translateY(-2px);
}
.otn-card:hover .otn-actions { opacity: 1; pointer-events: auto }
.otn-card--root { width: 300px }
.otn-card--lv-0 { border-color:#0EA5A6 }
.otn-card--lv-1 { border-color:#38BDF8 }
.otn-card--lv-2 { border-color:#60A5FA }
.otn-card--lv-3 { border-color:#818CF8 }
.otn-card--lv-4 { border-color:#A78BFA }
.otn-card--root .otn-header {
  padding: 12px 16px !important;
}
.otn-card--root .otn-name { font-size: 15px !important; font-weight: 700 !important; min-height: 40px }
.otn-card--lv-0 .otn-header { background: linear-gradient(135deg, #0F766E, #0D9488) }
.otn-card--lv-1 .otn-header { background: linear-gradient(135deg, #0284C7, #0EA5E9) }
.otn-card--lv-2 .otn-header { background: linear-gradient(135deg, #2563EB, #3B82F6) }
.otn-card--lv-3 .otn-header { background: linear-gradient(135deg, #4F46E5, #6366F1) }
.otn-card--lv-4 .otn-header { background: linear-gradient(135deg, #7C3AED, #8B5CF6) }

/* 根节点保留层级配色，但提高间距 */
.otn-card--root .otn-header {
  padding: 12px 16px !important;
}

/* Header */
.otn-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 11px 44px 11px 14px;
  background: linear-gradient(135deg, #0D9488, #14B8A6);
  user-select: none;
}
.otn-name {
  font-size: 14px;
  font-weight: 700;
  color: #fff;
  overflow: visible;
  white-space: normal;
  word-break: break-word;
  line-height: 1.35;
  flex: 1;
}
.otn-arrow {
  display: flex;
  color: rgba(255,255,255,0.8);
  transition: transform 0.25s ease;
  margin-left: 6px;
}
.otn-arrow--down { transform: rotate(0deg) }
.otn-arrow:not(.otn-arrow--down) { transform: rotate(-90deg) }

/* Body */
.otn-body { padding: 11px 14px 12px; display: flex; flex-direction: column; gap: 7px }
.otn-info { display: flex; align-items: flex-start; gap: 6px; font-size: 12px; color: #516276; line-height: 1.3 }
.otn-info span { white-space: normal; word-break: break-word }
.otn-info--clickable { cursor: pointer; border-radius: 4px; padding: 2px 4px; margin: -2px -4px; transition: background 0.15s }
.otn-info--clickable:hover { background: rgba(14,165,233,0.1) }
.otn-info--clickable:hover .otn-emp-count { color: #0EA5E9; text-decoration: underline }

/* Actions overlay */
.otn-actions {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  gap: 2px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}
.otn-act {
  width: 22px; height: 22px;
  border: none; border-radius: 5px;
  background: rgba(255,255,255,0.24);
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s;
}
.otn-act:hover { background: rgba(255,255,255,0.4) }
.otn-act--del:hover { background: rgba(239,68,68,0.7) }

/* Collapsed badge */
.otn-badge {
  margin-top: 8px;
  padding: 4px 11px;
  background: #F8FAFC;
  border: 1px dashed #BFD0E2;
  border-radius: 10px;
  font-size: 11px;
  color: #64748B;
  cursor: pointer;
}
.otn-badge:hover { background: #E2E8F0 }

/* ===== Children area ===== */
.otn-children {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
}

/* Vertical line from parent card down to horizontal rail */
.otn-vline {
  width: 2px;
  height: 26px;
  background: #AFC2D5;
}

/* Row of children */
.otn-row {
  display: flex;
  align-items: flex-start;
  position: relative;
  width: max-content;
  padding-top: 0;
}

/* Each child column */
.otn-child {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 14px 0;
  position: relative;
  z-index: 1;
}

/* Vertical line from horizontal rail down to child card */
.otn-child::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: 2px;
  height: 24px;
  transform: translateX(-50%);
  background: #AFC2D5;
}

.otn-child::after {
  content: '';
  position: absolute;
  top: 0;
  height: 2px;
  background: #AFC2D5;
}
.otn-child--only::after { display:none }
.otn-child--first:not(.otn-child--only)::after {
  left: 50%;
  right: 0;
}
.otn-child--last:not(.otn-child--only)::after {
  left: 0;
  right: 50%;
}
.otn-child:not(.otn-child--first):not(.otn-child--last)::after {
  left: 0;
  right: 0;
}

.otn-child--only {
  padding-top: 24px;
}

/* ===== Animation ===== */
.otn-children {
  animation: otnFadeIn 0.25s ease;
}
@keyframes otnFadeIn {
  from { opacity: 0; transform: translateY(-6px) }
  to { opacity: 1; transform: translateY(0) }
}
</style>
