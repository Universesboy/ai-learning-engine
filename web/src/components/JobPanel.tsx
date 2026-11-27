import { useEffect, useRef, useState } from 'react'
import { api, Job, JOB_ACTIVE, JobStage, post, pct } from '../api'

const STAGE_MARK: Record<JobStage['status'], string> = { pending: '', running: '…', waiting: '?', done: '✓', failed: '!', skipped: '–' }

function StageList({ stages }: { stages: JobStage[] }) {
  return (
    <ol className="stages">
      {stages.map(s => (
        <li key={s.stage} className={`stage st-${s.status}`}>
          <span className="stage-mark" aria-hidden="true">{STAGE_MARK[s.status]}</span>
          <span className="stage-label">{s.label}</span>
          <span className="faint stage-note">
            {s.status === 'running' && s.total ? `${s.done ?? 0} / ${s.total}` : ''}
            {s.error ? <span className="error">{s.error}</span> : s.note}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** Processing state of a book's latest import job: progress, cost confirmation, errors and retry, quality report. */
export function JobPanel({ bookId, onChange }: { bookId: number; onChange: () => void }) {
  const [job, setJob] = useState<Job | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [withAppendix, setWithAppendix] = useState(false)
  const [estimate, setEstimate] = useState<number | null>(null)
  const [open, setOpen] = useState(false)
  const seen = useRef<string>('')

  const load = () => api<Job | null>(`/books/${bookId}/job`).then(j => {
    setJob(j)
    const key = j ? `${j.status}:${j.chapters_done}:${j.stage}` : ''
    if (seen.current && key !== seen.current) onChange()  // chapters become studyable as they finish
    seen.current = key
  }).catch(e => setErr(e.message))
  useEffect(() => { load() }, [bookId]) // eslint-disable-line
  useEffect(() => {
    if (!job || !JOB_ACTIVE.includes(job.status)) return
    const t = setTimeout(load, 1500)
    return () => clearTimeout(t)
  }, [job]) // eslint-disable-line

  const appendix = job?.chapters.filter(c => c.kind === 'appendix' && c.map_status === 'not_planned') ?? []
  useEffect(() => {
    if (!job || job.status !== 'waiting') return
    if (!withAppendix) { setEstimate(job.estimate_usd); return }
    const ids = job.chapters.filter(c => c.map_status === 'pending' || appendix.includes(c)).map(c => c.id)
    api<{ usd: number }>(`/books/${bookId}/estimate?chapter_ids=${ids.join(',')}`).then(e => setEstimate(e.usd)).catch(() => {})
  }, [job?.status, withAppendix]) // eslint-disable-line

  if (!job) return null
  const act = async (fn: () => Promise<Job>) => {
    setBusy(true); setErr(null)
    try { setJob(await fn()); onChange() } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const confirm = () => act(() => post<Job>(`/jobs/${job.id}/confirm`, withAppendix
    ? { chapter_ids: job.chapters.filter(c => c.map_status === 'pending' || appendix.includes(c)).map(c => c.id) } : {}))
  const retry = () => act(() => post<Job>(`/jobs/${job.id}/retry`))
  const issues = job.report.issues ?? []
  const pending = job.chapters.filter(c => c.map_status === 'pending').length

  if (job.status === 'done' || job.status === 'partial') {
    if (!issues.length && job.status === 'done') return null
    return (
      <section className={`panel job${job.status === 'partial' ? ' job-warn' : ''}`}>
        <div className="row">
          <p className="grow">
            {job.status === 'partial'
              ? <><b>{job.chapters_failed.length} 章知识提取失败。</b>其余 {job.chapters_done} 章已经可以学习，失败的章节可以单独重试，不会重做已完成的部分。</>
              : <>处理完成：{job.chapters_done} 章，{job.concepts} 个知识点。质量检查有 {issues.length} 项提示。</>}
          </p>
          {job.status === 'partial' && <button className="btn primary small" disabled={busy} onClick={retry}>重试失败的 {job.chapters_failed.length} 章</button>}
          <button className="btn ghost small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '收起报告' : '查看质量报告'}</button>
        </div>
        {err && <p className="error">{err}</p>}
        {open && (
          <ul className="issues fade-in">
            {issues.map((i, n) => (
              <li key={n} className={`issue-${i.level}`}>{i.text}
                {i.items && <span className="faint" style={{ display: 'block' }}>{i.items.slice(0, 8).map(g => g.join(' / ')).join('；')}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    )
  }

  return (
    <section className={`panel job${job.status === 'failed' ? ' job-warn' : ''}`} aria-live="polite">
      <div className="row">
        <div className="grow">
          <h2>{job.status === 'waiting' ? '结构已识别，等你确认' : job.status === 'failed' ? '处理中断' : '正在处理这本书'}</h2>
          <p className="faint" style={{ marginTop: 2 }}>
            {job.status === 'waiting' && `已经切分出 ${pending} 章待整理的内容。整理知识点需要调用模型，确认后开始。`}
            {JOB_ACTIVE.includes(job.status) && (job.stage === 'knowledge'
              ? `正在整理知识点：${job.chapters_done} / ${job.chapters_total} 章，已提取 ${job.concepts} 个。完成的章节可以先学。可以离开这个页面，处理会在后台继续。`
              : '读取文本和章节结构，通常不到一分钟，不调用模型。')}
            {job.status === 'failed' && (job.error || '未知错误')}
          </p>
        </div>
        <b className="num job-pct">{pct(job.progress)}</b>
      </div>
      <div className="meter" style={{ margin: '12px 0 4px' }}><i style={{ width: pct(job.progress), background: 'var(--iris)' }} /></div>

      {job.status === 'waiting' && (
        <div className="row job-confirm">
          <button className="btn primary" disabled={busy || estimate == null} onClick={confirm}>
            {busy ? '正在开始……' : `开始整理，预计 $${(estimate ?? 0).toFixed(2)}`}
          </button>
          {appendix.length > 0 && (
            <label className="faint check"><input type="checkbox" checked={withAppendix} onChange={e => setWithAppendix(e.target.checked)} />
              同时整理 {appendix.length} 个附录</label>
          )}
          <span className="faint">按 Claude 官方价格估算，实际花费记在这本书的用量里。</span>
        </div>
      )}
      {job.status === 'failed' && <div className="row" style={{ marginTop: 10 }}><button className="btn primary small" disabled={busy} onClick={retry}>从失败的步骤继续</button></div>}
      {err && <p className="error">{err}</p>}

      <button className="btn ghost small" style={{ marginTop: 8 }} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '收起处理步骤' : '查看处理步骤'}</button>
      {open && <StageList stages={job.stages} />}
    </section>
  )
}
