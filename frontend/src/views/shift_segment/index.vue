<template>
  <section class="page" data-module="shift_segment">
    <header class="page-head">
      <div>
        <h2>采样责任段管理</h2>
        <p class="page-desc">按负责人交接时点把源端样本归属到班次，迟到回执经前班更正或交班账补录裁定，同一读数只计一笔。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记源端读数</button>
        <button class="btn" type="button" @click="exportRows">导出责任段清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-if="row.status === '待裁定'">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else>—</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采样责任段数据，可先登记源端读数</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采样责任段记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>

    <section class="report-block">
      <header class="page-head">
        <div>
          <h3>分班采集报告</h3>
          <p class="page-desc">依据采样责任段数据集分列各班负责的站点、观测分钟与样本编号，跨班补计反映在来源小计里。</p>
        </div>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="generateReport">生成交班采集报告</button>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in reportColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="shift in reportRows" :key="shift.班次编号">
            <td>{{ shift.班次编号 }}</td>
            <td>{{ shift.负责人 }}</td>
            <td>{{ shift.站点.join('、') || '—' }}</td>
            <td>{{ shift.有效观测分钟 }}</td>
            <td>{{ shift.可归责读数 }}</td>
            <td>{{ formatSubtotal(shift.来源小计) }}</td>
            <td>{{ shift.样本编号.join('、') || '—' }}</td>
          </tr>
          <tr v-if="!reportRows.length">
            <td :colspan="reportColumns.length" class="empty-state">暂无班次小结</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>{{ reportNote }}</span>
        <span>{{ reportCheck }}</span>
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记源端读数</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in createFields" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createForm[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type ShiftReportRow = {
  班次编号: string
  负责人: string
  责任时段: string
  交班账状态: string
  站点: string[]
  有效观测分钟: number
  可归责读数: number
  样本编号: string[]
  来源小计: Record<string, number>
  时间凭据: Array<Record<string, string>>
}

type ShiftReport = {
  口径: string
  班次: ShiftReportRow[]
  合计: { 可归责读数: number; 全局去重后读数: number; 校验: string }
}

const ENDPOINT = '/api/shift_segment'
const columns = ["段编号", "样本编号", "站点", "采样时间", "接入时间", "跨交接点", "归属依据", "采用时点", "班次编号", "计入方式", "原接入批次"]
const actions = ["前班更正", "交班账补录"]
const reportColumns = ["班次编号", "负责人", "站点", "有效观测分钟", "可归责读数", "来源小计", "样本编号"]
const stats = [{ label: "责任段总数", value: 0 }, { label: "待裁定", value: 0 }, { label: "跨交接点", value: 0 }, { label: "已计入读数", value: 0 }]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ["样本编号", "站点", "班次编号", "计入方式"]
const showCreateModal = ref(false)
const createFields = ["样本编号", "站点", "采样时间", "接入时间", "原接入批次"]
const createForm = ref<Record<string, string>>({})
const reportRows = ref<ShiftReportRow[]>([])
const reportNote = ref('')
const reportCheck = ref('')

function updateStats() {
  const data = rows.value
  stats[0].value = total.value
  stats[1].value = data.filter((r: Row) => r.status === '待裁定').length
  stats[2].value = data.filter((r: Row) => r['跨交接点'] === '是').length
  stats[3].value = data.filter((r: Row) => r['计入方式'] === '正常计入' || r['计入方式'] === '跨班补计' || r['计入方式'] === '更正补计').length
}

function formatSubtotal(subtotal: Record<string, number>) {
  return `正常${subtotal['正常计入'] ?? 0} · 补录${subtotal['跨班补计'] ?? 0} · 更正${subtotal['更正补计'] ?? 0} · 未计${subtotal['未计入'] ?? 0}`
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  noticeMessage.value = ''
  createForm.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '登记失败')
    }
    noticeMessage.value = data.message || '读数已登记'
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '采样责任段动作未生效，请稍后重试')
    }
    noticeMessage.value = data.message || `已完成${action}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采样责任段操作失败'
  }
}

async function loadReport() {
  try {
    const response = await request(`${ENDPOINT}/report`)
    if (!response.ok) {
      throw new Error('分班采集报告读取失败')
    }
    const payload = (await response.json()) as ShiftReport
    reportRows.value = payload.班次 ?? []
    reportNote.value = payload.口径 ?? ''
    reportCheck.value = payload.合计
      ? `合计可归责 ${payload.合计.可归责读数} 笔，全局去重后 ${payload.合计.全局去重后读数} 笔，校验${payload.合计.校验}`
      : ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分班采集报告读取失败'
  }
}

async function generateReport() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/report`, { method: 'POST' })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '交班采集报告生成失败')
    }
    noticeMessage.value = data.message || '交班采集报告已生成'
    await loadReport()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交班采集报告生成失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('采样责任段列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
    await loadReport()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采样责任段列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.report-block {
  margin-top: 20px;
}
.notice-text {
  color: #067647;
}
</style>
