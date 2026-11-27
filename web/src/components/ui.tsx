import { ReactNode, useEffect, useState } from 'react'
import { Status, STATUS_LABEL, STATUS_ORDER, api, post } from '../api'

const I = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
)
export const Icon = {
  overview: I(<><rect x="3" y="3" width="7" height="9" rx="2" /><rect x="14" y="3" width="7" height="5" rx="2" /><rect x="14" y="12" width="7" height="9" rx="2" /><rect x="3" y="16" width="7" height="5" rx="2" /></>),
  library: I(<><path d="M4 19V5a2 2 0 0 1 2-2h3v18H6a2 2 0 0 1-2-2Z" /><path d="M9 3h5v18H9" /><path d="m14 4 4.5-1 3 17-4.5 1" /></>),
  review: I(<><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v4h4" /><path d="M12 8v4l3 2" /></>),
  graph: I(<><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="9" cy="18" r="2.5" /><path d="m8.2 7.2 7.5.6M7 8.3l1.4 7.3M16.4 10l-5.6 6.4" /></>),
  cross: I(<><path d="M4 5h6a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4z" /><path d="M20 5h-6a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h6z" /></>),
  insights: I(<><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>),
  upload: I(<><path d="M12 15V3m0 0-4 4m4-4 4 4" /><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" /></>),
}

export function BrandMark() {
  // concentric "development" rings: faint outline → saturated core
  return (
    <svg className="brand-mark" viewBox="0 0 26 26" aria-hidden="true">
      <circle cx="13" cy="13" r="11.5" fill="none" stroke="var(--m-none)" strokeWidth="1.5" />
      <circle cx="13" cy="13" r="7.5" fill="none" stroke="var(--m-apply)" strokeWidth="1.5" />
      <circle cx="13" cy="13" r="3.5" fill="var(--m-mastered)" />
    </svg>
  )
}

export const Dot = ({ status }: { status: Status }) => <span className={`dot st-${status}`} title={STATUS_LABEL[status]} />
export const Pill = ({ status }: { status: Status }) => (
  <span className={`pill st-${status}`}><span className={`dot st-${status}`} />{STATUS_LABEL[status]}</span>
)

export function Legend() {
  return <div className="legend">{STATUS_ORDER.map(s => <span key={s}><Dot status={s} />{STATUS_LABEL[s]}</span>)}</div>
}

/** stacked bar of mastery states */
export function StatusMeter({ counts, total }: { counts: Partial<Record<Status, number>>; total: number }) {
  if (!total) return <div className="meter" />
  const color: Record<Status, string> = {
    mastered: 'var(--m-mastered)', cannot_apply_yet: 'var(--m-apply)', shaky: 'var(--m-shaky)', needs_relearning: 'var(--m-relearn)', not_started: 'transparent',
  }
  return (
    <div className="meter" role="img" aria-label={STATUS_ORDER.map(s => `${STATUS_LABEL[s]} ${counts[s] || 0}`).join('，')}>
      {STATUS_ORDER.map(s => <i key={s} style={{ width: `${(100 * (counts[s] || 0)) / total}%`, background: color[s] }} />)}
    </div>
  )
}

export function Cover({ id, title, large }: { id: number; title: string; large?: boolean }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className={`cover${large ? ' large' : ''}`}>
      {!failed && <img src={`/api/books/${id}/cover`} alt="" loading="lazy" onError={() => setFailed(true)} />}
      {failed && <div className="cover-type"><span>{title}</span></div>}
    </div>
  )
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div className="skeleton" style={{ height: h }} />
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="empty">
      <p className="error">{message}</p>
      {retry && <button className="btn small" style={{ marginTop: 10 }} onClick={retry}>重新加载</button>}
    </div>
  )
}

interface RepairState {
  running: boolean; total: number; done: number; failed: { title: string; error: string }[]
  pending: number; estimate_usd: number
}

/** Shown only while chapters damaged by the pre-2.0 key collision remain. */
export function RepairBanner({ onDone }: { onDone?: () => void }) {
  const [s, setS] = useState<RepairState | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => api<RepairState>('/maintenance/repair').then(setS).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!s?.running) return
    const t = setTimeout(async () => {
      const n = await api<RepairState>('/maintenance/repair')
      setS(n)
      if (!n.running) onDone?.()
    }, 2500)
    return () => clearTimeout(t)
  }, [s, onDone])
  if (!s || (!s.running && s.pending === 0)) return null
  const start = async () => {
    setErr(null)
    try { await post('/maintenance/repair'); load() } catch (e: any) { setErr(e.message) }
  }
  return (
    <div className="banner" role="status">
      {s.running ? (
        <p>正在修复知识地图：{s.done} / {s.total} 章完成。可以离开这个页面，修复会在后台继续。</p>
      ) : (
        <p>
          <b>{s.pending} 章知识地图需要修复。</b>旧版本里，不同章节的同名知识点会被合并，导致部分知识点缺失。
          修复会重新阅读这些章节，预计花费 ${s.estimate_usd.toFixed(2)}，已有答题记录不受影响。
          {s.failed.length > 0 && <span className="error"> 上次有 {s.failed.length} 章失败，可以再试一次。</span>}
          {err && <span className="error"> {err}</span>}
        </p>
      )}
      {!s.running && <button className="btn primary small" onClick={start}>修复 {s.pending} 章</button>}
    </div>
  )
}

export function ComingSoon({ title, phase, intro, items }: { title: string; phase: string; intro: string; items: string[] }) {
  return (
    <div className="soon-page fade-in">
      <div className="page-head"><div><span className="soon-badge">开发中（{phase}）</span><h1 style={{ marginTop: 12 }}>{title}</h1></div></div>
      <div className="panel">
        <p className="muted">{intro}</p>
        <ul className="soon-list">{items.map(i => <li key={i}>{i}</li>)}</ul>
        <p className="faint" style={{ marginTop: 16 }}>这个页面目前没有数据，完成后会直接读取你的知识库，不会显示示例数据。</p>
      </div>
    </div>
  )
}

export function Toast({ text, onClose }: { text: string | null; onClose: () => void }) {
  useEffect(() => { if (text) { const t = setTimeout(onClose, 3500); return () => clearTimeout(t) } }, [text, onClose])
  return text ? <div className="toast" role="status">{text}</div> : null
}
