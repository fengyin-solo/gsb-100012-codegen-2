<template>
  <section class="page" data-module="task">
    <header class="page-head">
      <div>
        <h2>检测任务管理</h2>
        <p class="page-desc">维护检测任务单，支持多选后一次提交批量分配任务、开始检测、提交复核，逐条返回处理结果。</p>
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

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label class="filter-item">
        <span>任务编号</span>
        <input v-model="keyword" placeholder="按任务编号检索" />
      </label>
      <label class="filter-item">
        <span>任务状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="batch-bar">
      <span class="hint-text">
        已选 {{ selectedIds.size }} 条<template v-if="selectedIds.size">（跨页选择会一直保留，直到清空或提交）</template>
      </span>
      <button
        v-for="action in actions"
        :key="action"
        class="btn"
        type="button"
        :disabled="!selectedIds.size || submitting"
        @click="runBatch(action)"
      >
        批量{{ action }}
      </button>
      <button class="btn ghost" type="button" :disabled="!selectedIds.size || submitting" @click="clearSelection">
        清空选择
      </button>
      <span v-if="submitting" class="hint-text">批量提交中，请勿重复点击…</span>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th class="check-col">
            <input type="checkbox" :checked="allPageSelected" :disabled="!rows.length" @change="togglePage" />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="check-col">
            <input
              type="checkbox"
              :checked="selectedIds.has(Number(row.id))"
              @change="toggleRow(Number(row.id))"
            />
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="submitting"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无检测任务数据，可先登记检测任务单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测任务记录，第 {{ page }} / {{ pageCount }} 页</span>
      <span class="pager">
        <button class="btn ghost" type="button" :disabled="page <= 1" @click="gotoPage(page - 1)">上一页</button>
        <button class="btn ghost" type="button" :disabled="page >= pageCount" @click="gotoPage(page + 1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="batchReceipt" class="result-panel">
      <header>
        <strong>{{ batchReceipt.message }}</strong>
        <button class="link" type="button" @click="batchReceipt = null">收起结果</button>
      </header>
      <ul>
        <li
          v-for="item in batchReceipt.results"
          :key="item.id"
          :class="item.ok ? (item.skipped ? 'skip-text' : 'ok-text') : 'error-text'"
        >
          {{ item.ok ? (item.skipped ? '⊘' : '✓') : '✗' }} {{ item.label }}：{{ item.message }}
        </li>
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface BatchItem {
  id: number
  label: string
  ok: boolean
  skipped: boolean
  message: string
}

interface BatchReceipt {
  ok: boolean
  action: string
  message: string
  total: number
  succeeded: number
  skipped: number
  failed: number
  results: BatchItem[]
}

const ENDPOINT = '/api/task'
const columns = ["任务编号", "所属样品", "检测项目", "检测标准", "指定检测员", "截止日期", "优先级", "任务状态"]
const actions = ["分配任务", "开始检测", "提交复核"]
const statuses = ["待分配", "已分配", "检测中", "已完成", "已复核"]
const PAGE_SIZE = 20

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const statusFilter = ref('')
const stats = ref([
  { label: '待分配任务', value: 0 },
  { label: '检测中任务', value: 0 },
  { label: '逾期任务', value: 0 },
])
// 用 Set 按 id 记选择，翻页、刷新列表都不丢，天然支持跨页多选
const selectedIds = ref<Set<number>>(new Set())
const submitting = ref(false)
const batchReceipt = ref<BatchReceipt | null>(null)
const errorMessage = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const allPageSelected = computed(
  () => rows.value.length > 0 && rows.value.every((row) => selectedIds.value.has(Number(row.id))),
)

function toggleRow(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function togglePage() {
  const next = new Set(selectedIds.value)
  if (allPageSelected.value) {
    rows.value.forEach((row) => next.delete(Number(row.id)))
  } else {
    rows.value.forEach((row) => next.add(Number(row.id)))
  }
  selectedIds.value = next
}

function clearSelection() {
  selectedIds.value = new Set()
}

function applyFilters() {
  page.value = 1
  void reload()
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  page.value = 1
  void reload()
}

function gotoPage(target: number) {
  if (target < 1 || target > pageCount.value) {
    return
  }
  page.value = target
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测任务单登记入口尚未接入审批流'
}

async function runBatch(action: string) {
  // 防重复提交：提交途中直接忽略后续点击
  if (submitting.value) {
    return
  }
  if (!selectedIds.value.size) {
    errorMessage.value = '请先勾选要操作的检测任务单'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  batchReceipt.value = null
  try {
    const response = await request(`${ENDPOINT}/batch-actions`, {
      method: 'POST',
      body: JSON.stringify({ action, ids: [...selectedIds.value] }),
    })
    const receipt = (await response.json()) as BatchReceipt
    if (!response.ok) {
      throw new Error(receipt.message || `批量${action}未生效，请稍后重试`)
    }
    batchReceipt.value = receipt
    // 成功与跳过的记录移出选择，失败的保留勾选，方便修正后直接重试
    const remaining = new Set(selectedIds.value)
    receipt.results.forEach((item) => {
      if (item.ok) {
        remaining.delete(item.id)
      }
    })
    selectedIds.value = remaining
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : `批量${action}失败`
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  if (submitting.value) {
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '检测任务动作未生效，请稍后重试')
    }
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务操作失败'
  } finally {
    submitting.value = false
  }
}

async function refreshAll() {
  // 列表与统计一起刷新，保证提交后清单、指标卡、操作状态口径一致
  await Promise.all([reload(), loadSummary()])
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    stats.value = [
      { label: '待分配任务', value: payload['待分配'] ?? 0 },
      { label: '检测中任务', value: payload['检测中'] ?? 0 },
      { label: '逾期任务', value: payload['逾期'] ?? 0 },
    ]
  } catch {
    // 指标卡读取失败不打断页面，列表错误信息统一走 errorMessage
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) {
    query.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    query.set('status', statusFilter.value)
  }
  query.set('page', String(page.value))
  query.set('size', String(PAGE_SIZE))
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('检测任务单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    // 批量操作后当前页可能已空（例如按状态过滤时记录被流转走），自动回退到最后一页
    if (!rows.value.length && total.value > 0 && page.value > pageCount.value) {
      page.value = pageCount.value
      await reload()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务列表读取失败'
  }
}

onMounted(refreshAll)
</script>
