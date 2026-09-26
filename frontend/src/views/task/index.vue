<template>
  <section class="page" data-module="task">
    <header class="page-head">
      <div>
        <h2>检测任务管理</h2>
        <p class="page-desc">维护检测任务单，围绕任务编号、所属样品、检测项目、检测标准做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检测任务单</button>
        <button class="btn" type="button" @click="exportRows">导出检测任务清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="search">
      <label class="filter-item">
        <span>任务编号</span>
        <input v-model="filters.keyword" placeholder="按任务编号检索" />
      </label>
      <label class="filter-item">
        <span>任务状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="batch-bar">
      <div class="selection-info">
        <strong>已选 {{ selectedCount }} 条</strong>
        <span>选择会跨页保留</span>
        <button v-if="selectedCount" class="link" type="button" @click="clearSelection">清空选择</button>
      </div>
      <div class="batch-actions">
        <button
          v-for="action in actions"
          :key="action"
          class="btn primary"
          type="button"
          :disabled="!selectedCount || batchLoading"
          @click="runBatch(action)"
        >
          {{ batchLoading && activeBatchAction === action ? `正在${action}...` : `批量${action}` }}
        </button>
      </div>
    </div>

    <div v-if="batchResult" class="result-panel" :class="{ 'has-failure': batchResult.failed > 0 }">
      <div class="result-summary">
        <strong>{{ batchResult.message }}</strong>
        <span v-if="batchResult.duplicates">；自动去除重复选择 {{ batchResult.duplicates }} 条</span>
        <span v-if="batchResult.idempotent">；命中重复提交保护，未重复执行</span>
        <button class="link" type="button" @click="batchResult = null">关闭结果</button>
      </div>
      <ul class="result-list">
        <li v-for="item in batchResult.items" :key="item.id" :class="item.ok ? 'is-ok' : 'is-fail'">
          <span>{{ item.ok ? '成功' : '失败' }}</span>
          <span>#{{ item.id }}</span>
          <span>{{ item.message }}</span>
        </li>
      </ul>
    </div>

    <table class="data-table task-table">
      <thead>
        <tr>
          <th class="select-cell">
            <input
              type="checkbox"
              :checked="isPageAllSelected"
              :disabled="!rows.length"
              @change="toggleCurrentPage"
            />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="select-cell">
            <input
              type="checkbox"
              :checked="selectedIds.has(Number(row.id))"
              @change="toggleRow(Number(row.id), ($event.target as HTMLInputElement).checked)"
            />
          </td>
          <td v-for="column in columns" :key="column">
            <span v-if="column === '任务状态'" class="status-tag">{{ row[column] ?? '—' }}</span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="!canRunAction(action, row)"
              :title="actionTitle(action, row)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">
            {{ loading ? '检测任务清单加载中...' : '暂无符合条件的检测任务数据，可先登记检测任务单' }}
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot task-foot">
      <div class="pager">
        <button class="btn" type="button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button class="btn" type="button" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
      </div>
      <span>共 {{ total }} 条检测任务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type RowValue = string | number | boolean | null
type Row = {
  id: number
  status?: string
  任务状态?: string
  [key: string]: RowValue | undefined
}

type Metrics = {
  total: number
  pending_assignment: number
  in_progress: number
  overdue: number
}

type ListPayload = {
  items: Row[]
  total: number
  page: number
  size: number
}

type BatchItemResult = {
  id: number
  ok: boolean
  message: string
  entry: Row | null
}

type BatchResponse = {
  ok: boolean
  action: string
  total: number
  requested: number
  succeeded: number
  failed: number
  duplicates: number
  idempotent: boolean
  message: string
  items: BatchItemResult[]
}

const ENDPOINT = '/api/task'
const PAGE_SIZE = 20
const columns = ["任务编号", "所属样品", "检测项目", "检测标准", "指定检测员", "截止日期", "优先级", "任务状态"]
const actions = ["分配任务", "开始检测", "提交复核"] as const
const statuses = ["待分配", "已分配", "检测中", "已完成", "已复核"]
const allowedStatuses: Record<string, string[]> = {
  分配任务: ["待分配"],
  开始检测: ["已分配"],
  提交复核: ["检测中", "已完成"],
}

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const errorMessage = ref('')
const filters = ref({ keyword: '', status: '' })
const selectedIds = ref(new Set<number>())
const batchLoading = ref(false)
const activeBatchAction = ref('')
const batchResult = ref<BatchResponse | null>(null)
const metrics = ref<Metrics>({ total: 0, pending_assignment: 0, in_progress: 0, overdue: 0 })

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const selectedCount = computed(() => selectedIds.value.size)
const isPageAllSelected = computed(() => (
  rows.value.length > 0 && rows.value.every(row => selectedIds.value.has(Number(row.id)))
))
const stats = computed(() => [
  { label: "检测任务总数", value: metrics.value.total },
  { label: "待分配任务", value: metrics.value.pending_assignment },
  { label: "检测中任务", value: metrics.value.in_progress },
  { label: "逾期任务", value: metrics.value.overdue },
])

let latestLoadId = 0

function search() {
  page.value = 1
  void reload()
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  search()
}

function goPage(targetPage: number) {
  page.value = Math.min(Math.max(targetPage, 1), totalPages.value)
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测任务单登记入口尚未接入审批流'
}

function toggleRow(id: number, checked: boolean) {
  const next = new Set(selectedIds.value)
  if (checked) {
    next.add(id)
  } else {
    next.delete(id)
  }
  selectedIds.value = next
}

function toggleCurrentPage(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(selectedIds.value)
  rows.value.forEach(row => {
    const id = Number(row.id)
    if (checked) {
      next.add(id)
    } else {
      next.delete(id)
    }
  })
  selectedIds.value = next
}

function clearSelection() {
  selectedIds.value = new Set<number>()
}

function rowStatus(row: Row) {
  return String(row.status ?? row.任务状态 ?? '')
}

function canRunAction(action: string, row: Row) {
  return allowedStatuses[action]?.includes(rowStatus(row)) ?? false
}

function actionTitle(action: string, row: Row) {
  return canRunAction(action, row) ? action : `当前状态为「${rowStatus(row) || '未知'}」，不能${action}`
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '检测任务动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务操作失败'
  }
}

async function runBatch(action: string) {
  const ids = Array.from(selectedIds.value)
  if (!ids.length || batchLoading.value) {
    return
  }

  errorMessage.value = ''
  batchResult.value = null
  batchLoading.value = true
  activeBatchAction.value = action
  const idempotencyKey = createIdempotencyKey()

  try {
    const response = await request(`${ENDPOINT}/batch/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, ids, idempotency_key: idempotencyKey }),
    })
    const payload = await response.json().catch(() => null) as BatchResponse | { detail?: string } | null
    if (!response.ok || !payload || !('items' in payload)) {
      throw new Error(payload && 'detail' in payload ? payload.detail : '批量操作未完成，请稍后重试')
    }

    batchResult.value = payload
    const failedIds = new Set(payload.items.filter(item => !item.ok).map(item => item.id))
    selectedIds.value = new Set(ids.filter(id => failedIds.has(id)))
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量操作失败'
  } finally {
    batchLoading.value = false
    activeBatchAction.value = ''
  }
}

async function reload() {
  const loadId = ++latestLoadId
  errorMessage.value = ''
  loading.value = true

  const params = new URLSearchParams({
    page: String(page.value),
    size: String(PAGE_SIZE),
  })
  if (filters.value.keyword.trim()) {
    params.set('keyword', filters.value.keyword.trim())
  }
  if (filters.value.status) {
    params.set('status', filters.value.status)
  }

  try {
    const [listResponse, metricsResponse] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}/metrics`),
    ])
    const listPayload = await listResponse.json().catch(() => null) as ListPayload | null
    const metricsPayload = await metricsResponse.json().catch(() => null) as Metrics | null

    if (!listResponse.ok || !metricsResponse.ok || !listPayload || !metricsPayload) {
      throw new Error('检测任务单或数量指标读取失败')
    }
    if (loadId !== latestLoadId) {
      return
    }

    rows.value = listPayload.items ?? []
    total.value = listPayload.total ?? 0
    page.value = listPayload.page ?? page.value
    metrics.value = metricsPayload
  } catch (error) {
    if (loadId !== latestLoadId) {
      return
    }
    errorMessage.value = error instanceof Error ? error.message : '检测任务列表读取失败'
  } finally {
    if (loadId === latestLoadId) {
      loading.value = false
    }
  }
}

function createIdempotencyKey() {
  const randomId = globalThis.crypto?.randomUUID?.() ?? String(Date.now() + Math.random())
  return `task-batch-${Date.now()}-${randomId}`
}

onMounted(reload)
</script>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.selection-info,
.batch-actions,
.pager,
.result-summary {
  display: flex;
  align-items: center;
  gap: 10px;
}

.selection-info {
  font-size: 13px;
}

.selection-info span {
  color: var(--muted);
}

.select-cell {
  width: 42px;
  text-align: center;
}

.task-table .link:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef4ff;
  color: #1d4ed8;
}

.result-panel {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #ecfdf3;
  border: 1px solid #a6f4c5;
  border-radius: 8px;
  font-size: 13px;
}

.result-panel.has-failure {
  background: #fffaeb;
  border-color: #fedf89;
}

.result-summary {
  justify-content: flex-start;
}

.result-summary .link {
  margin-left: auto;
}

.result-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  max-height: 160px;
  overflow: auto;
}

.result-list li {
  display: grid;
  grid-template-columns: 48px 72px 1fr;
  gap: 8px;
  padding: 4px 0;
}

.result-list .is-ok span:first-child {
  color: #027a48;
  font-weight: 600;
}

.result-list .is-fail span:first-child {
  color: #b42318;
  font-weight: 600;
}

.task-foot {
  align-items: center;
}

.pager {
  font-size: 12px;
}

.pager .btn {
  padding: 4px 10px;
}

@media (max-width: 900px) {
  .batch-bar {
    align-items: stretch;
    flex-direction: column;
  }

  .batch-actions {
    flex-wrap: wrap;
  }
}
</style>
