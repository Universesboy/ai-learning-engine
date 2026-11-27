export class ApiError extends Error {}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch('/api' + path, opts)
  const text = await r.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) throw new ApiError((data && data.detail) || `请求失败（${r.status}）`)
  return data as T
}
export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export type Status = 'not_started' | 'needs_relearning' | 'shaky' | 'cannot_apply_yet' | 'mastered'
export const STATUS_LABEL: Record<Status, string> = {
  not_started: '尚无测试', needs_relearning: '需要重学', shaky: '理解不牢', cannot_apply_yet: '还不会应用', mastered: '已掌握',
}
export const STATUS_ORDER: Status[] = ['mastered', 'cannot_apply_yet', 'shaky', 'needs_relearning', 'not_started']
export const ERR_LABEL: Record<string, string> = {
  none: '—', misconception: '概念误解', incomplete: '回答不完整', calculation: '计算错误', misread_question: '审题错误',
  cannot_transfer: '不会迁移应用', forgot_fact: '记忆遗忘',
}
export const LEVEL_LABEL: Record<string, string> = { recall: '记忆', understand: '理解', apply: '应用' }
export const QTYPE_LABEL: Record<string, string> = {
  single_choice: '单选', true_false: '判断', fill_in: '填空', short_answer: '简答', application: '应用题', case_analysis: '案例分析',
}
export const DOMAINS: [string, string][] = [
  ['general', '通用'], ['psychology', '心理学'], ['investing', '投资'], ['economics', '经济学'], ['math_physics', '数学物理'],
  ['history', '历史'], ['programming', '编程'], ['language', '语言'],
]
export const domainLabel = (d?: string | null) => DOMAINS.find(x => x[0] === d)?.[1] ?? d ?? ''

export interface BookSummary {
  id: number; title: string; author: string | null; category: string | null; domain: string; created_at: string
  page_count: number | null; chapters: number; chapters_mapped: number; concepts: number; mastered: number; started: number
  weak: number; due: number; progress: number; ai_status: 'done' | 'partial' | 'not_started'; last_studied: string | null
  needs_repair: number
}
export interface Concept {
  id: number; book_id: number; chapter_id: number; name: string; kind: string; form: string; pages: number[]
  summary: string; status: Status; mastery: number; can_apply: boolean; due: string | null; forgetting_risk: number | null
}

export function fmtDate(s?: string | null) {
  if (!s) return '—'
  const d = new Date(s.includes('T') ? s : s.replace(' ', 'T') + 'Z')
  return isNaN(+d) ? s : d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
export function fmtDateTime(s?: string | null) {
  if (!s) return '—'
  const d = new Date(s.includes('T') ? s : s.replace(' ', 'T') + 'Z')
  return isNaN(+d) ? s : d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
export const pct = (x: number) => `${Math.round(x * 100)}%`
