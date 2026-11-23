/** 报价差异复核接口封装：字段差异、复核单、按单取样、当前复验和质检台账。 */
import { request } from './client'

export type PageResult<T> = {
  items: T[]
  total: number
  page: number
  size: number
}

export type QuoteDiff = {
  id: number
  记录编号: string
  报价编号: string
  字段名: string
  前值: string | number | null
  后值: string | number | null
  基线版本: string
  来源版本: string
  变更类型: string
  检测时间: string
}

export type ReviewTicket = {
  复核单编号: string
  报价编号: string
  字段名: string
  前值: string | number | null
  后值: string | number | null
  基线版本: string
  来源版本: string
  状态: string
  锁定: boolean
  样本编号: string | null
  创建人: string
  创建时间: string
}

export type TicketDetail = {
  ticket: ReviewTicket & { id: number; 提交幂等键?: string }
  snapshot: {
    字段名: string
    前值: string | number | null
    后值: string | number | null
    基线版本: string
    来源版本: string
  }
  live: { 当前版本: string | null; 当前值: string | number | null; 存在: boolean }
  version_changed: boolean
  note: string
}

export type LedgerEntry = {
  台账编号: string
  复核单编号: string
  报价编号: string
  字段名: string
  样本值: string | number | null
  发布版本: string
  基线值: string | number | null
  基线版本: string
  样本类型: string
  入样: boolean
  结果: string
  理由: string
  取样人: string
  取样时间: string
}

export type TicketBatchResult = {
  ok: boolean
  duplicate: boolean
  created: (ReviewTicket & { id: number })[]
  reused: ReviewTicket[]
  message: string
}

export type SampleResult = {
  ok: boolean
  duplicate: boolean
  ticket: ReviewTicket & { id: number }
  sample: LedgerEntry | null
  message: string
}

export type ReverifyResult = {
  ok: boolean
  field: string
  baseline_version: string
  current_version: string
  quota: number
  candidate_count: number
  candidates: {
    报价编号: string
    当前值: string | number | null
    基线值: string | number | null
    复核单编号?: string
  }[]
  sampled: LedgerEntry[]
  not_sampled: LedgerEntry[]
  anomaly_quotes: string[]
  pending_quotes: string[]
  old_samples: (LedgerEntry & { id: number })[]
  quality_plan: { 计划编号: string; 目标批次: string; 检验结果: string; status: string }
  message: string
}

export type QuoteHistory = {
  报价编号: string
  字段名: string | null
  timeline: {
    批次版本: string
    父版本: string
    存在: boolean
    值: string | number | null
    相比上一发布版本有变化: boolean | null
    上一版本: string | null
    发布时间: string | null
  }[]
  diffs: QuoteDiff[]
  tickets: ReviewTicket[]
  samples: LedgerEntry[]
}

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' ? (payload as { detail?: unknown }).detail : null
    throw new Error(typeof detail === 'string' ? detail : fallback)
  }
  return payload as T
}

export async function prepareDemo(): Promise<{ ok: boolean; message: string }> {
  const response = await request('/api/qc/demo/bootstrap', { method: 'POST' })
  return jsonOrThrow(response, '本地报价批次准备失败')
}

export async function publishCorrection(): Promise<{ ok: boolean; message: string }> {
  const response = await request('/api/qc/demo/correction', { method: 'POST' })
  return jsonOrThrow(response, '更正批发布失败')
}

export async function queryDiffs(params: {
  field: string
  version?: string
  quoteId?: string
  page?: number
  size?: number
}): Promise<PageResult<QuoteDiff>> {
  const query = new URLSearchParams({ field: params.field })
  if (params.version) query.set('version', params.version)
  if (params.quoteId) query.set('quote_id', params.quoteId)
  query.set('page', String(params.page ?? 1))
  query.set('size', String(params.size ?? 20))
  const response = await request(`/api/qc/diffs?${query.toString()}`)
  return jsonOrThrow(response, '字段差异读取失败')
}

export async function createTickets(payload: {
  diff_ids?: number[]
  field?: string
  version?: string
  quote_id?: string
  idempotency_key?: string
}): Promise<TicketBatchResult> {
  const response = await request('/api/qc/review-tickets', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return jsonOrThrow(response, '复核单登记失败')
}

export async function listTickets(params: {
  status?: string
  page?: number
  size?: number
} = {}): Promise<PageResult<ReviewTicket & { id: number }>> {
  const query = new URLSearchParams()
  if (params.status) query.set('status', params.status)
  query.set('page', String(params.page ?? 1))
  query.set('size', String(params.size ?? 50))
  const response = await request(`/api/qc/review-tickets?${query.toString()}`)
  return jsonOrThrow(response, '复核单读取失败')
}

export async function getTicket(ticketId: string): Promise<TicketDetail> {
  const response = await request(`/api/qc/review-tickets/${encodeURIComponent(ticketId)}`)
  return jsonOrThrow(response, '复核单详情读取失败')
}

export async function sampleTicket(ticketId: string): Promise<SampleResult> {
  const response = await request(`/api/qc/review-tickets/${encodeURIComponent(ticketId)}/sample`, {
    method: 'POST',
  })
  return jsonOrThrow(response, '按原单取样失败')
}

export async function reverify(params: {
  field: string
  baseline_version: string
  current_version?: string
  quota?: number
}): Promise<ReverifyResult> {
  const response = await request('/api/qc/reverify', {
    method: 'POST',
    body: JSON.stringify(params),
  })
  return jsonOrThrow(response, '当前复验失败')
}

export async function getQuoteHistory(quoteId: string, field?: string): Promise<QuoteHistory> {
  const query = new URLSearchParams()
  if (field) query.set('field', field)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await request(`/api/qc/quotes/${encodeURIComponent(quoteId)}/history${suffix}`)
  return jsonOrThrow(response, '报价发布轨迹读取失败')
}

export async function listLedger(params: {
  field?: string
  quote_id?: string
  ticket_id?: string
  sample_type?: string
  page?: number
  size?: number
} = {}): Promise<PageResult<LedgerEntry>> {
  const query = new URLSearchParams()
  if (params.field) query.set('field', params.field)
  if (params.quote_id) query.set('quote_id', params.quote_id)
  if (params.ticket_id) query.set('ticket_id', params.ticket_id)
  if (params.sample_type) query.set('sample_type', params.sample_type)
  query.set('page', String(params.page ?? 1))
  query.set('size', String(params.size ?? 50))
  const response = await request(`/api/qc/ledger?${query.toString()}`)
  return jsonOrThrow(response, '质检台账读取失败')
}
