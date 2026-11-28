import { useEffect, useState } from 'react'
import { api, Job, JOB_ACTIVE, post, pct } from '../api'

interface Links {
  stats: { total: number; cross_book: number; by_type: Record<string, number>; checks: Record<string, number> }
  books: { id: number; title: string; concepts: number; embedded: number; summarized: number }[]
  job: Job | null
}

/** Starts and follows the job that links books: free embeddings, then (after the cost OK) chapter
 *  summaries and relation discovery. Hidden when every book with concepts is already linked. */
export function LinkPanel({ onDone }: { onDone: () => void }) {
  const [d, setD] = useState<Links | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const load = () => api<Links>('/links').then(setD).catch(e => setErr(e.message))
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!d?.job || !JOB_ACTIVE.includes(d.job.status)) return
    const t = setTimeout(async () => {
      const n = await api<Links>('/links')
      setD(n)
      if (n.job && !JOB_ACTIVE.includes(n.job.status)) onDone()
    }, 2000)
    return () => clearTimeout(t)
  }, [d, onDone])
  if (!d) return null
  const job = d.job
  const unlinked = d.books.filter(b => b.concepts > 0 && b.embedded < b.concepts)
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null)
    try { await fn(); await load() } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const start = () => act(() => post('/links', { book_ids: d.books.filter(b => b.concepts > 0).map(b => b.id) }))

  if (job && JOB_ACTIVE.includes(job.status)) {
    const st = job.stages.find(s => s.status === 'running')
    return (
      <section className="panel job" aria-live="polite">
        <div className="row"><p className="grow"><b>正在建立关联。</b>{st?.label}{st?.total ? `：${st.done} / ${st.total}` : ''}。{st?.note ?? ''} 可以离开这个页面。</p>
          <b className="num job-pct">{pct(job.progress)}</b></div>
        <div className="meter" style={{ marginTop: 10 }}><i style={{ width: pct(job.progress), background: 'var(--iris)' }} /></div>
      </section>
    )
  }
  if (job?.status === 'waiting') {
    const note = job.stages.find(s => s.status === 'waiting')?.note
    return (
      <section className="panel job">
        <p><b>知识点已在本机向量化，等你确认。</b>{note}。章节要点和关系判断用 Claude Haiku，花费记在各本书的用量里。</p>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={busy} onClick={() => act(() => post(`/jobs/${job.id}/confirm`))}>开始建立关联，预计 ${(job.estimate_usd ?? 0).toFixed(2)}</button>
        </div>
        {err && <p className="error">{err}</p>}
      </section>
    )
  }
  if (job?.status === 'failed') return (
    <section className="panel job job-warn">
      <p><b>建立关联中断：</b>{job.error}</p>
      <div className="row" style={{ marginTop: 10 }}><button className="btn primary small" disabled={busy} onClick={start}>重新开始（已判断过的不会重复付费）</button></div>
      {err && <p className="error">{err}</p>}
    </section>
  )
  if (!unlinked.length) return null
  return (
    <section className="panel job">
      <p><b>{unlinked.length} 本书还没有建立关联：</b>{unlinked.map(b => `《${b.title}》`).join('')}。
        先在本机计算知识点向量（不花钱），再告诉你章节要点和关系判断的预计花费。</p>
      <div className="row" style={{ marginTop: 10 }}><button className="btn primary" disabled={busy} onClick={start}>{busy ? '正在开始……' : '建立关联'}</button></div>
      {err && <p className="error">{err}</p>}
    </section>
  )
}
