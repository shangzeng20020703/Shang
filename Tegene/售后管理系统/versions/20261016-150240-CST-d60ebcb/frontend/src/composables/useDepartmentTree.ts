import { computed, ref } from 'vue'
import { get } from '@/utils/request'

const rawDepartments = ref<any[]>([])
const departmentTree = ref<any[]>([])
const loading = ref(false)
const lastFetchedAt = ref(0)
let inflight: Promise<void> | null = null

function normalizeList(res: any): any[] {
  return res?.items || res?.data || res || []
}

function buildDepartmentTree(list: any[]) {
  const map = new Map<number, any>()
  for (const d of list || []) map.set(d.id, { ...d, children: [] })
  const roots: any[] = []
  for (const d of list || []) {
    const node = map.get(d.id)
    if (d.parent_id && map.has(d.parent_id)) map.get(d.parent_id).children.push(node)
    else roots.push(node)
  }
  const sortNodes = (nodes: any[]) => {
    nodes.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
    nodes.forEach((n) => sortNodes(n.children || []))
  }
  sortNodes(roots)
  return roots
}

async function doFetchDepartments(force = false): Promise<void> {
  const now = Date.now()
  if (!force && rawDepartments.value.length && now - lastFetchedAt.value < 30_000) return
  loading.value = true
  try {
    const res = await get('/departments')
    const list = normalizeList(res)
    rawDepartments.value = list
    departmentTree.value = buildDepartmentTree(list)
    lastFetchedAt.value = Date.now()
  } catch {
    rawDepartments.value = []
    departmentTree.value = []
  } finally {
    loading.value = false
  }
}

async function refreshDepartments(force = false): Promise<void> {
  if (inflight && !force) {
    await inflight
    return
  }
  inflight = doFetchDepartments(force)
  try {
    await inflight
  } finally {
    inflight = null
  }
}

export function invalidateDepartmentTreeCache() {
  lastFetchedAt.value = 0
}

export function useDepartmentTree() {
  const rootExpandedKeys = computed<number[]>(() => (departmentTree.value || []).map((n: any) => n.id))

  const ensureDepartmentsLoaded = async () => {
    await refreshDepartments(false)
  }

  const refreshDepartmentTree = async () => {
    await refreshDepartments(true)
  }

  const onDepartmentSelectVisibleChange = async (visible: boolean) => {
    if (!visible) return
    await refreshDepartments(true)
  }

  return {
    departments: rawDepartments,
    departmentTree,
    departmentTreeLoading: loading,
    rootExpandedKeys,
    ensureDepartmentsLoaded,
    refreshDepartmentTree,
    onDepartmentSelectVisibleChange,
  }
}
