import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, BookSummary, DOMAINS, domainLabel, fmtDate } from '../api'
import { useApi } from '../hooks'
import { Cover, ErrorBox, Icon, RepairBanner, Skeleton, StatusMeter, Toast } from '../components/ui'

function AiState({ b }: { b: BookSummary }) {
  if (b.needs_repair) return <span className="ai-state"><span className="dot st-shaky" />{b.needs_repair} 章需要修复</span>
  if (b.ai_status === 'done') return <span className="ai-state"><span className="dot st-mastered" />AI 已整理全部 {b.chapters} 章</span>
  if (b.ai_status === 'partial') return <span className="ai-state"><span className="dot st-cannot_apply_yet" />已整理 {b.chapters_mapped} / {b.chapters} 章</span>
  return <span className="ai-state"><span className="dot st-not_started" />尚未整理</span>
}

function Upload({ onDone }: { onDone: (id: number, dup: boolean) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [domain, setDomain] = useState('general')
  const send = async (f?: File) => {
    if (!f) return
    if (!/\.pdf$/i.test(f.name)) { setErr('目前只支持 PDF 文件'); return }
    setErr(null); setBusy(`正在导入「${f.name}」，识别章节和页码……`)
    const fd = new FormData(); fd.append('file', f)
    try {
      const r = await api<{ book_id: number; duplicate: boolean }>(
        `/books?domain=${domain}&title=${encodeURIComponent(f.name.replace(/\.pdf$/i, ''))}`, { method: 'POST', body: fd })
      onDone(r.book_id, r.duplicate)
    } catch (e: any) { setErr(e.message) } finally { setBusy(null) }
  }
  return (
    <div
      className={`panel drop${over ? ' over' : ''}`}
      onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
      onDrop={e => { e.preventDefault(); setOver(false); send(e.dataTransfer.files[0]) }}
    >
      <h3>导入书籍</h3>
      <p className="faint">把 PDF 拖到这里，或选择文件。同一个文件不会重复导入。</p>
      <select className="select" value={domain} onChange={e => setDomain(e.target.value)} aria-label="领域">
        {DOMAINS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <button className="btn" disabled={!!busy} onClick={() => input.current?.click()}>{Icon.upload}<span>选择 PDF</span></button>
      <input ref={input} type="file" accept="application/pdf" hidden onChange={e => send(e.target.files?.[0])} />
      {busy && <p className="faint">{busy}</p>}
      {err && <p className="error" style={{ fontSize: 13 }}>{err}</p>}
    </div>
  )
}

export default function Library() {
  const { data, error, reload } = useApi<BookSummary[]>('/library')
  const [toast, setToast] = useState<string | null>(null)
  const nav = useNavigate()
  if (error) return <ErrorBox message={error} retry={reload} />
  return (
    <div className="fade-in">
      <div className="page-head">
        <div><h1>知识库</h1><p className="muted">每本书一个知识档案：结构、知识点、掌握情况和复习需求。</p></div>
      </div>
      <RepairBanner onDone={reload} />
      {!data ? <div className="grid books"><Skeleton h={210} /><Skeleton h={210} /></div> : (
        <div className="grid books">
          {data.map(b => (
            <Link key={b.id} to={`/library/${b.id}`} className="panel book">
              <div className="book-top">
                <Cover id={b.id} title={b.title} />
                <div className="book-meta">
                  <h3>{b.title}</h3>
                  <span className="faint">{[b.author || '作者未填写', b.category || domainLabel(b.domain)].join('，')}</span>
                  <span className="faint">导入于 {fmtDate(b.created_at)}，{b.chapters} 章</span>
                  <AiState b={b} />
                </div>
              </div>
              <div className="book-stats">
                <div><b className="num">{b.concepts}</b>知识点</div>
                <div><b className="num">{b.mastered}</b>已掌握</div>
                <div><b className="num">{b.due}</b>待复习</div>
              </div>
              <div>
                <StatusMeter counts={{ mastered: b.mastered }} total={b.concepts} />
                <p className="faint" style={{ marginTop: 6 }}>
                  {b.started ? `已测试 ${b.started} 个知识点，${b.weak} 个薄弱` : '还没有开始测试'}
                </p>
              </div>
            </Link>
          ))}
          <Upload onDone={(id, dup) => { if (dup) setToast('这本书已经在知识库里了'); nav(`/library/${id}`) }} />
        </div>
      )}
      <Toast text={toast} onClose={() => setToast(null)} />
    </div>
  )
}
