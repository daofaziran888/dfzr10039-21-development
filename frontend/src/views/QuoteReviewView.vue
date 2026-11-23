<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>报价差异复核</h2>
        <p class="page-desc">
          按字段读取报价批次差异，从检索结果开字段差异复核单，再按原单取样；样本回写质检台账。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn ghost" type="button" :disabled="busy" @click="onPrepare">准备本地批次</button>
        <button class="btn" type="button" :disabled="busy" @click="onCorrect">发布更正批</button>
      </div>
    </header>

    <p v-if="message" class="notice">{{ message }}</p>
    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <form class="filter-bar" @submit.prevent="search(1)">
      <label class="filter-item">
        <span>差异字段</span>
        <select v-model="field" data-testid="field-select">
          <option value="金额">金额</option>
          <option value="标题">标题</option>
        </select>
      </label>
      <label class="filter-item">
        <span>来源版本（可空）</span>
        <input v-model="version" data-testid="version-filter" placeholder="如 V1" />
      </label>
      <label class="filter-item">
        <span>报价编号（可空）</span>
        <input v-model="quoteFilter" data-testid="quote-filter" placeholder="如 A" />
      </label>
      <label class="filter-item">
        <span>每页</span>
        <input v-model.number="size" type="number" min="1" max="200" data-testid="size-filter" />
      </label>
      <button class="btn primary" data-testid="search-diff" type="submit">查询字段差异</button>
      <button class="btn" data-testid="load-history" type="button" :disabled="!quoteFilter" @click="loadHistory">
        查报价发布轨迹
      </button>
    </form>

    <h3 class="section-title">字段差异（字段 / 前值 / 后值 / 来源版本）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th><input type="checkbox" data-testid="select-all" @change="toggleAll" /></th>
          <th>记录编号</th>
          <th>报价编号</th>
          <th>字段名</th>
          <th>前值</th>
          <th>后值</th>
          <th>基线版本</th>
          <th>来源版本</th>
          <th>变更类型</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in diffs" :key="row.id" :data-testid="`diff-${row.记录编号}`">
          <td><input v-model="selected" type="checkbox" :value="row.id" /></td>
          <td>{{ row.记录编号 }}</td>
          <td>{{ row.报价编号 }}</td>
          <td>{{ row.字段名 }}</td>
          <td>{{ row.前值 ?? '—' }}</td>
          <td>{{ row.后值 ?? '—' }}</td>
          <td>{{ row.基线版本 }}</td>
          <td>{{ row.来源版本 }}</td>
          <td>{{ row.变更类型 }}</td>
        </tr>
        <tr v-if="!diffs.length">
          <td colspan="9" class="empty-state">暂无字段差异，可先准备本地报价批次</td>
        </tr>
      </tbody>
    </table>
    <div class="page-foot">
      <span>共 {{ diffTotal }} 处差异，第 {{ diffPage }} / {{ diffPages }} 页</span>
      <span class="pager">
        <button class="btn ghost" type="button" :disabled="diffPage <= 1" @click="search(diffPage - 1)">
          上一页
        </button>
        <button
          class="btn ghost"
          type="button"
          :disabled="diffPage >= diffPages"
          @click="search(diffPage + 1)"
        >
          下一页
        </button>
      </span>
    </div>
    <div class="page-actions">
      <button class="btn primary" data-testid="open-tickets" type="button" :disabled="!selected.length" @click="openTickets">
        从选中差异开复核单
      </button>
      <button
        class="btn"
        data-testid="open-tickets-by-field"
        type="button"
        :disabled="!field"
        @click="openTicketsByField"
      >
        按当前字段整批开单
      </button>
    </div>

    <h3 class="section-title">字段差异复核单</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>复核单编号</th>
          <th>报价编号</th>
          <th>字段名</th>
          <th>前值</th>
          <th>后值</th>
          <th>基线版本</th>
          <th>来源版本</th>
          <th>状态</th>
          <th>样本编号</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in tickets" :key="row.id" :data-testid="`ticket-${row.复核单编号}`">
          <td>{{ row.复核单编号 }}</td>
          <td>{{ row.报价编号 }}</td>
          <td>{{ row.字段名 }}</td>
          <td>{{ row.前值 ?? '—' }}</td>
          <td>{{ row.后值 ?? '—' }}</td>
          <td>{{ row.基线版本 }}</td>
          <td>{{ row.来源版本 }}</td>
          <td>{{ row.状态 }}</td>
          <td>{{ row.样本编号 ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="inspectTicket(row.复核单编号)">回查原单</button>
            <button
              class="link"
              type="button"
              :disabled="row.状态 === '已取样'"
              :data-testid="`sample-${row.复核单编号}`"
              @click="takeSample(row.复核单编号)"
            >
              按原单取样
            </button>
          </td>
        </tr>
        <tr v-if="!tickets.length">
          <td colspan="10" class="empty-state">暂无复核单</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-card" data-testid="ticket-detail">
      <h4>复核单 {{ detail.ticket.复核单编号 }} 原单快照</h4>
      <dl>
        <dt>字段</dt><dd>{{ detail.snapshot.字段名 }}</dd>
        <dt>前值 / 基线版本</dt><dd>{{ detail.snapshot.前值 }} / {{ detail.snapshot.基线版本 }}</dd>
        <dt>后值 / 来源版本</dt><dd>{{ detail.snapshot.后值 }} / {{ detail.snapshot.来源版本 }}</dd>
        <dt>当前版本实时值</dt><dd>{{ detail.live.当前值 ?? '—' }}（{{ detail.live.当前版本 }}）</dd>
        <dt>单据状态</dt><dd>{{ detail.ticket.状态 }}，锁定 {{ detail.ticket.锁定 ? '是' : '否' }}</dd>
      </dl>
      <p v-if="detail.note" class="notice">{{ detail.note }}</p>
    </section>

    <section v-if="history" class="detail-card" data-testid="quote-history">
      <h4>报价 {{ history.报价编号 }} 的发布轨迹{{ history.字段名 ? `（${history.字段名}）` : '' }}</h4>
      <table class="data-table">
        <thead>
          <tr><th>批次版本</th><th>父版本</th><th>存在</th><th>值</th><th>较上一版变化</th><th>发布时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in history.timeline" :key="row.批次版本">
            <td>{{ row.批次版本 }}</td>
            <td>{{ row.父版本 || '—' }}</td>
            <td>{{ row.存在 ? '是' : '否' }}</td>
            <td>{{ row.值 ?? '—' }}</td>
            <td>{{ row.相比上一发布版本有变化 === null ? '—' : row.相比上一发布版本有变化 ? '是' : '否' }}</td>
            <td>{{ row.发布时间 ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="page-desc">
        关联复核单 {{ history.tickets.length }} 份，样本 {{ history.samples.length }} 条
      </p>
    </section>

    <h3 class="section-title">当前复验</h3>
    <form class="filter-bar" @submit.prevent="runReverify">
      <label class="filter-item">
        <span>复验字段</span>
        <select v-model="reverifyField" data-testid="reverify-field">
          <option value="金额">金额</option>
          <option value="标题">标题</option>
        </select>
      </label>
      <label class="filter-item">
        <span>基线版本</span>
        <input v-model="baselineVersion" data-testid="baseline-version" />
      </label>
      <label class="filter-item">
        <span>样本名额</span>
        <input v-model.number="quota" type="number" min="0" data-testid="quota" />
      </label>
      <button class="btn primary" data-testid="run-reverify" type="submit">运行当前复验</button>
    </form>

    <section v-if="reverifyResult" class="detail-card" data-testid="reverify-result">
      <h4>{{ reverifyResult.message }}</h4>
      <p>
        基线 {{ reverifyResult.baseline_version }} → 当前 {{ reverifyResult.current_version }}；
        本轮异常报价：{{ reverifyResult.anomaly_quotes.join('、') || '无' }}
      </p>
      <p v-if="reverifyResult.pending_quotes.length">
        更正后仍有差异：{{ reverifyResult.pending_quotes.join('、') }}
      </p>
      <table class="data-table">
        <thead>
          <tr>
            <th>报价编号</th>
            <th>样本值</th>
            <th>发布版本</th>
            <th>基线值</th>
            <th>入样</th>
            <th>结果</th>
            <th>理由</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in reverifyRows" :key="row.台账编号 + row.样本类型">
            <td>{{ row.报价编号 }}</td>
            <td>{{ row.样本值 ?? '—' }}</td>
            <td>{{ row.发布版本 }}</td>
            <td>{{ row.基线值 ?? '—' }}（{{ row.基线版本 }}）</td>
            <td>{{ row.入样 ? '是' : '否' }}</td>
            <td>{{ row.结果 }}</td>
            <td>{{ row.理由 }}</td>
          </tr>
        </tbody>
      </table>
      <p class="page-desc">
        质检计划 {{ reverifyResult.quality_plan.计划编号 }}（{{ reverifyResult.quality_plan.目标批次 }}）：
        {{ reverifyResult.quality_plan.检验结果 }}，状态 {{ reverifyResult.quality_plan.status }}
      </p>
    </section>

    <h3 class="section-title">质检台账</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>台账编号</th>
          <th>复核单编号</th>
          <th>报价编号</th>
          <th>字段名</th>
          <th>样本值</th>
          <th>发布版本</th>
          <th>基线值</th>
          <th>样本类型</th>
          <th>入样</th>
          <th>结果</th>
          <th>理由</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in ledger" :key="row.台账编号" :data-testid="`ledger-${row.台账编号}`">
          <td>{{ row.台账编号 }}</td>
          <td>{{ row.复核单编号 || '—' }}</td>
          <td>{{ row.报价编号 }}</td>
          <td>{{ row.字段名 }}</td>
          <td>{{ row.样本值 ?? '—' }}</td>
          <td>{{ row.发布版本 || '—' }}</td>
          <td>{{ row.基线值 ?? '—' }}</td>
          <td>{{ row.样本类型 }}</td>
          <td>{{ row.入样 ? '是' : '否' }}</td>
          <td>{{ row.结果 }}</td>
          <td>{{ row.理由 }}</td>
        </tr>
        <tr v-if="!ledger.length">
          <td colspan="11" class="empty-state">暂无质检台账记录</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createTickets,
  getQuoteHistory,
  getTicket,
  listLedger,
  listTickets,
  prepareDemo,
  publishCorrection,
  queryDiffs,
  reverify as runReverifyApi,
  sampleTicket,
  type LedgerEntry,
  type QuoteDiff,
  type QuoteHistory,
  type ReviewTicket,
  type ReverifyResult,
  type TicketDetail,
} from '@/api/quote-review'

const busy = ref(false)
const message = ref('')
const errorMessage = ref('')

const field = ref('金额')
const version = ref('')
const quoteFilter = ref('')
const size = ref(20)
const diffs = ref<QuoteDiff[]>([])
const diffTotal = ref(0)
const diffPage = ref(1)
const selected = ref<number[]>([])
const diffPages = computed(() => Math.max(1, Math.ceil(diffTotal.value / Math.max(1, size.value))))

const tickets = ref<(ReviewTicket & { id: number })[]>([])
const ledger = ref<LedgerEntry[]>([])
const detail = ref<TicketDetail | null>(null)
const history = ref<QuoteHistory | null>(null)

const reverifyField = ref('金额')
const baselineVersion = ref('V0')
const quota = ref(2)
const reverifyResult = ref<ReverifyResult | null>(null)
const reverifyRows = computed(() => {
  const result = reverifyResult.value
  if (!result) return []
  return [...result.sampled, ...result.not_sampled]
})

async function guard(task: () => Promise<void>) {
  busy.value = true
  errorMessage.value = ''
  try {
    await task()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    busy.value = false
  }
}

async function refreshSidePanels() {
  const [ticketPage, ledgerPage] = await Promise.all([
    listTickets(),
    listLedger({ size: 200 }),
  ])
  tickets.value = ticketPage.items
  ledger.value = ledgerPage.items
}

async function search(page = 1) {
  await guard(async () => {
    if (page < 1 || page > diffPages.value) page = 1
    const result = await queryDiffs({
      field: field.value,
      version: version.value || undefined,
      quoteId: quoteFilter.value || undefined,
      page,
      size: size.value,
    })
    diffs.value = result.items
    diffTotal.value = result.total
    diffPage.value = result.page
    selected.value = []
    message.value = `「${field.value}」共 ${result.total} 处差异`
  })
}

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selected.value = checked ? diffs.value.map((row) => row.id) : []
}

async function openTickets() {
  await guard(async () => {
    const result = await createTickets({ diff_ids: selected.value })
    await refreshSidePanels()
    message.value = result.message
    selected.value = []
  })
}

async function openTicketsByField() {
  await guard(async () => {
    const result = await createTickets({
      field: field.value,
      version: version.value || undefined,
      quote_id: quoteFilter.value || undefined,
    })
    await refreshSidePanels()
    message.value = result.message
  })
}

async function loadHistory() {
  await guard(async () => {
    history.value = await getQuoteHistory(quoteFilter.value, field.value)
    message.value = `已读取报价 ${quoteFilter.value} 的发布轨迹`
  })
}

async function inspectTicket(ticketId: string) {
  await guard(async () => {
    detail.value = await getTicket(ticketId)
  })
}

async function takeSample(ticketId: string) {
  await guard(async () => {
    const result = await sampleTicket(ticketId)
    await refreshSidePanels()
    detail.value = await getTicket(ticketId)
    message.value = result.message
  })
}

async function runReverify() {
  await guard(async () => {
    reverifyResult.value = await runReverifyApi({
      field: reverifyField.value,
      baseline_version: baselineVersion.value,
      quota: quota.value,
    })
    await refreshSidePanels()
    message.value = reverifyResult.value.message
  })
}

async function onPrepare() {
  await guard(async () => {
    const result = await prepareDemo()
    detail.value = null
    history.value = null
    reverifyResult.value = null
    await refreshSidePanels()
    await search(1)
    message.value = result.message
  })
}

async function onCorrect() {
  await guard(async () => {
    const result = await publishCorrection()
    await refreshSidePanels()
    message.value = result.message
    if (detail.value) detail.value = await getTicket(detail.value.ticket.复核单编号)
  })
}

onMounted(async () => {
  await refreshSidePanels()
  await search(1)
})
</script>

<style scoped>
.section-title { margin: 20px 0 8px; font-size: 15px; }
.notice { background: #eef4ff; border: 1px solid #c7d7fe; color: #1e3a8a; padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.detail-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin: 10px 0; }
.detail-card dl { display: grid; grid-template-columns: 160px 1fr; gap: 6px 12px; font-size: 13px; margin: 8px 0; }
.detail-card dt { color: var(--muted); }
.detail-card dd { margin: 0; }
.pager { display: flex; gap: 6px; }
.page-actions { display: flex; gap: 8px; margin: 8px 0 12px; }
</style>
