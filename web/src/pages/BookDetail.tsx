import { Fragment, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, BookSummary, Concept, ERR_LABEL, KIND_LABEL, LEVEL_LABEL, STATUS_LABEL, STATUS_ORDER, Status, domainLabel, fmtDate, patch, post, pct } from '../api'
import { useApi } from '../hooks'
import { Cover, Dot, ErrorBox, Legend, Pill, RepairBanner, Skeleton, StatusMeter } from '../components/ui'
import { JobPanel } from '../components/JobPanel'
import { RelationCard, Relation } from '../components/Relations'

interface Chapter {
  id: number; ord: number; title: string; page_start: number; page_end: number; concepts: number; mastered: number
  started: number; weak: number; due: number; needs_repair: boolean; map_built_at: string | null; summary: string | null
  kind: string | null; part: string | null; map_status: string | null; map_error: string | null
  core_ideas: { text: string; pages: number[] }[]; arguments: { claim: string; evidence: string; pages: number[] }[]
  cases: { name: string; point: string; pages: number[] }[]
}
interface Detail {
  book: { id: number; title: string; author: string | null; category: string | null; domain: string; created_at: string; license: string | null }
  summary: BookSummary
  chapters: Chapter[]
  concepts: Concept[]
  path: { id: number; name: string; chapter_id: number }[]
  mistakes: { id: number; concept: string; prompt: string; response: string; feedback: string; error_type: string; level: string; created_at: string }[]
  usage: { usd: number; calls: number; items: { purpose: string; model: string; calls: number; usd: number | null }[] }
}

const TABS = ['章节结构', '知识点', '学习路径', '错题本', '个人笔记', '章节联系'] as const
type Tab = typeof TABS[number]
const PURPOSE: Record<string, string> = { knowledge: '知识地图', teach: '讲解', practice: '出题', grading: '判分', visual: '交互演示', chapter: '章节要点', relations: '关系判断', crossbook: '跨书问答' }

function EditDialog({ d, onSaved }: { d: Detail; onSaved: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const [f, setF] = useState({ title: d.book.title, author: d.book.author ?? '', category: d.book.category ?? '' })
  const [busy, setBusy] = useState(false)
  return (
    <>
      <button className="btn small" onClick={() => ref.current?.showModal()}>编辑书籍信息</button>
      <dialog ref={ref}>
        <form method="dialog" onSubmit={async e => {
          e.preventDefault(); setBusy(true)
          try { await patch(`/books/${d.book.id}`, f); ref.current?.close(); onSaved() } finally { setBusy(false) }
        }}>
          <h2>编辑书籍信息</h2>
          <label className="field">书名<input className="input" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} required /></label>
          <label className="field">作者<input className="input" value={f.author} onChange={e => setF({ ...f, author: e.target.value })} placeholder="例如：丹尼尔·卡尼曼" /></label>
          <label className="field">分类<input className="input" value={f.category} onChange={e => setF({ ...f, category: e.target.value })} placeholder="例如：行为经济学" /></label>
          <div className="row" style={{ marginTop: 18, justifyContent: 'flex-end' }}>
            <button type="button" className="btn ghost" onClick={() => ref.current?.close()}>取消</button>
            <button className="btn primary" disabled={busy}>保存</button>
          </div>
        </form>
      </dialog>
    </>
  )
}

function ManageDialog({ d, onDone }: { d: Detail; onDone: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const nav = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [reocr, setReocr] = useState(false)
  const history = d.summary.started > 0
  const go = async (what: 'reprocess' | 'delete') => {
    setBusy(what); setErr(null)
    try {
      if (what === 'reprocess') { await post(`/books/${d.book.id}/reprocess?reocr=${reocr}`); ref.current?.close(); onDone() }
      else { await api(`/books/${d.book.id}`, { method: 'DELETE' }); ref.current?.close(); nav('/library') }
    } catch (e: any) { setErr(e.message) } finally { setBusy(null) }
  }
  return (
    <>
      <button className="btn small ghost" onClick={() => ref.current?.showModal()}>重新处理或删除</button>
      <dialog ref={ref}>
        <h2>重新处理或删除</h2>
        <p className="muted" style={{ marginTop: 8 }}>
          重新处理会用最新的导入规则重新识别章节、切分内容，原来的章节和知识点会被替换，然后停在确认费用那一步。适合章节识别错了、扫描版没识别出文字的书。
        </p>
        {history && <p className="error" style={{ marginTop: 8 }}>这本书已经有学习记录，为了不丢记录，重新处理和删除都不可用。</p>}
        <label className="check faint" style={{ marginTop: 12 }}><input type="checkbox" checked={reocr} onChange={e => setReocr(e.target.checked)} />
          重新提取文字（扫描版重新做 OCR，较慢）</label>
        {err && <p className="error" style={{ marginTop: 8 }}>{err}</p>}
        <div className="row" style={{ marginTop: 18, justifyContent: 'flex-end' }}>
          <button className="btn ghost" onClick={() => ref.current?.close()}>取消</button>
          <button className="btn" disabled={history || !!busy} onClick={() => go('delete')} style={{ color: 'var(--m-relearn)' }}>{busy === 'delete' ? '正在删除……' : '删除这本书'}</button>
          <button className="btn primary" disabled={history || !!busy} onClick={() => go('reprocess')}>{busy === 'reprocess' ? '正在开始……' : '重新处理'}</button>
        </div>
      </dialog>
    </>
  )
}

function ChapterCard({ ch, concepts, onMapped }: { ch: Chapter; concepts: Concept[]; onMapped: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const build = async () => {
    setBusy(true); setErr(null)
    try { await post(`/chapters/${ch.id}/map`); onMapped() } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="panel chapter">
      <div className="chapter-head" onClick={() => concepts.length && setOpen(!open)} role={concepts.length ? 'button' : undefined}
        tabIndex={concepts.length ? 0 : undefined} aria-expanded={concepts.length ? open : undefined}
        onKeyDown={e => { if (e.key === 'Enter' && concepts.length) setOpen(!open) }}>
        <h3>{ch.title}{ch.kind && ch.kind !== 'body' && <span className="pill" style={{ marginLeft: 8, verticalAlign: 2 }}>{KIND_LABEL[ch.kind] ?? ch.kind}</span>}</h3>
        {concepts.length > 0 && <div className="chapter-dots" aria-hidden="true">{concepts.map(c => <Dot key={c.id} status={c.status} />)}</div>}
        <span className="faint num" style={{ minWidth: 92, textAlign: 'right' }}>p.{ch.page_start}–{ch.page_end}</span>
      </div>
      <div className="row faint" style={{ marginTop: 6 }}>
        {ch.concepts ? <span>{ch.mastered} / {ch.concepts} 已掌握{ch.weak ? `，${ch.weak} 个薄弱` : ''}{ch.due ? `，${ch.due} 个待复习` : ''}</span>
          : <span className={ch.map_status === 'failed' ? 'error' : undefined}>{
            ch.map_status === 'running' ? '正在整理本章……'
              : ch.map_status === 'failed' ? `整理失败：${ch.map_error ?? '未知错误'}`
                : ch.map_status === 'not_planned' ? '默认不整理，需要时可以单独整理'
                  : ch.map_built_at ? '本章没有提取到知识点' : '还没有建立知识地图'}</span>}
        {ch.needs_repair && <span className="pill st-shaky">需要修复</span>}
        <span className="spacer" />
        {!ch.concepts && ch.map_status !== 'running' && <button className="btn small" disabled={busy} onClick={build}>{busy ? '正在阅读本章……' : ch.map_status === 'failed' ? '重新整理' : '建立知识地图'}</button>}
      </div>
      {err && <p className="error" style={{ fontSize: 13, marginTop: 6 }}>{err}</p>}
      {open && (
        <div className="fade-in">
          {ch.summary ? (
            <div className="chapter-notes">
              <p style={{ marginTop: 12 }}>{ch.summary}</p>
              {ch.core_ideas.length > 0 && <><h4>核心思想</h4><ul>{ch.core_ideas.map((x, i) => <li key={i}>{x.text}<span className="faint"> p.{x.pages.join(', ')}</span></li>)}</ul></>}
              {ch.arguments.length > 0 && <><h4>关键论证</h4><ul>{ch.arguments.map((x, i) => <li key={i}><b>{x.claim}</b> {x.evidence}<span className="faint"> p.{x.pages.join(', ')}</span></li>)}</ul></>}
              {ch.cases.length > 0 && <><h4>案例</h4><ul>{ch.cases.map((x, i) => <li key={i}><b>{x.name}</b>：{x.point}<span className="faint"> p.{x.pages.join(', ')}</span></li>)}</ul></>}
              <h4>知识点</h4>
            </div>
          ) : <p className="faint" style={{ marginTop: 12 }}>本章还没有整理核心思想和论证，在知识图谱页建立关联时会一起整理。下面是已提取的知识点。</p>}
          <div className="concept-grid">
            {concepts.map(c => (
              <Link key={c.id} to={`/concept/${c.id}`} className="concept-chip">
                <Dot status={c.status} /><span>{c.name}<span className="faint" style={{ display: 'block' }}>p.{c.pages.join(', ')}</span></span>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ConceptTable({ concepts, chapters }: { concepts: Concept[]; chapters: Chapter[] }) {
  const [q, setQ] = useState('')
  const [st, setSt] = useState<Status | 'all'>('all')
  const chTitle = useMemo(() => new Map(chapters.map(c => [c.id, c.title])), [chapters])
  const list = concepts.filter(c => (st === 'all' || c.status === st) && (!q || c.name.includes(q) || c.summary?.includes(q)))
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder="搜索知识点" value={q} onChange={e => setQ(e.target.value)} aria-label="搜索知识点" />
        <select className="select" style={{ maxWidth: 160 }} value={st} onChange={e => setSt(e.target.value as Status | 'all')} aria-label="按状态筛选">
          <option value="all">全部状态</option>
          {STATUS_ORDER.map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
        <span className="faint">{list.length} 个</span>
      </div>
      <div className="panel table-wrap" style={{ padding: 0 }}>
        <table className="data">
          <thead><tr><th>知识点</th><th>章节</th><th>状态</th><th>掌握度</th><th>遗忘风险</th><th>页码</th></tr></thead>
          <tbody>
            {list.slice(0, 300).map(c => (
              <tr key={c.id}>
                <td><Link to={`/concept/${c.id}`} style={{ fontWeight: 550 }}>{c.name}</Link></td>
                <td className="faint">{chTitle.get(c.chapter_id)}</td>
                <td><Pill status={c.status} /></td>
                <td className="num">{c.status === 'not_started' ? '—' : pct(c.mastery)}</td>
                <td className="num">{c.forgetting_risk == null ? '—' : pct(c.forgetting_risk)}</td>
                <td className="faint num">{c.pages.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.length > 300 && <p className="faint" style={{ marginTop: 8 }}>只显示前 300 个，用搜索缩小范围。</p>}
    </>
  )
}

function BookRelations({ bookId }: { bookId: number }) {
  const { data, error } = useApi<Relation[]>(`/books/${bookId}/relations`)
  const [n, setN] = useState(30)
  if (error) return <ErrorBox message={error} />
  if (!data) return <Skeleton h={200} />
  if (!data.length) return <div className="empty">还没有发现有原文依据的联系。到<Link to="/graph" style={{ color: 'var(--iris)' }}>知识图谱</Link>为这本书建立关联。</div>
  const cross = data.filter(r => r.cross_book).length
  return (
    <div className="stack">
      <p className="faint">{data.length} 条联系，其中 {cross} 条与其他书有关。每条都由 AI 读过双方原文判断，引用已核对。</p>
      <div className="rel-grid">{data.slice(0, n).map(r => <RelationCard key={r.id} r={r} self={-1} />)}</div>
      {data.length > n && <button className="btn small" onClick={() => setN(n + 30)}>再显示 30 条</button>}
    </div>
  )
}

export default function BookDetail() {
  const { id } = useParams()
  const { data: d, error, reload } = useApi<Detail>(`/books/${id}`)
  const [tab, setTab] = useState<Tab>('章节结构')
  const [jobKey, setJobKey] = useState(0)
  if (error) return <ErrorBox message={error} retry={reload} />
  if (!d) return <div className="stack"><Skeleton h={180} /><Skeleton h={400} /></div>
  const s = d.summary
  const counts: Partial<Record<Status, number>> = {}
  d.concepts.forEach(c => { counts[c.status] = (counts[c.status] || 0) + 1 })
  const byChapter = (chId: number) => d.concepts.filter(c => c.chapter_id === chId)

  return (
    <div className="fade-in">
      <p style={{ marginBottom: 16 }}><Link to="/library" className="faint">知识库</Link></p>
      <RepairBanner onDone={reload} />
      <section className="book-hero">
        <Cover id={d.book.id} title={d.book.title} large />
        <div className="grow">
          <h1>{d.book.title}</h1>
          <p className="muted">{d.book.author || '作者未填写'}，{d.book.category || domainLabel(d.book.domain)}，导入于 {fmtDate(d.book.created_at)}</p>
          <div className="figures" style={{ marginTop: 6 }}>
            <div className="figure"><b className="num">{s.chapters}</b><span className="faint">章，AI 已整理 {s.chapters_mapped}</span></div>
            <div className="figure"><b className="num">{s.concepts}</b><span className="faint">知识点</span></div>
            <div className="figure"><b className="num">{s.mastered}</b><span className="faint">已掌握，{pct(s.progress)}</span></div>
            <div className="figure"><b className="num">{s.due}</b><span className="faint">今日待复习</span></div>
          </div>
          <StatusMeter counts={counts} total={s.concepts} />
          <div className="row"><Legend /><span className="spacer" /><ManageDialog d={d} onDone={() => { reload(); setJobKey(k => k + 1) }} /><EditDialog d={d} onSaved={reload} /></div>
          <p className="faint">这本书累计花费 ${d.usage.usd.toFixed(2)}（{d.usage.items.map(u => `${PURPOSE[u.purpose] ?? u.purpose} ${u.calls} 次`).join('，') || '暂无调用记录'}）。2.0 之前的调用没有按书记录。</p>
        </div>
      </section>

      <JobPanel key={jobKey} bookId={d.book.id} onChange={reload} />

      <div className="tabs" role="tablist">
        {TABS.map(t => <button key={t} role="tab" className="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>)}
      </div>

      {tab === '章节结构' && (
        <div className="chapters">
          {d.chapters.map((ch, i) => (
            <Fragment key={ch.id}>
              {ch.part && ch.part !== d.chapters[i - 1]?.part && <h3 className="part-title">{ch.part}</h3>}
              <ChapterCard ch={ch} concepts={byChapter(ch.id)} onMapped={reload} />
            </Fragment>
          ))}
        </div>
      )}
      {tab === '知识点' && <ConceptTable concepts={d.concepts} chapters={d.chapters} />}
      {tab === '学习路径' && (
        <div className="panel">
          <p className="faint" style={{ marginBottom: 10 }}>按前置知识排序：先学被依赖的知识点。目前只有章内的前置关系，跨章关系在 Phase 4 加入。</p>
          <ol style={{ margin: 0, paddingLeft: 22, columns: '260px', columnGap: 32 }}>
            {d.path.map(p => {
              const c = d.concepts.find(x => x.id === p.id)
              return <li key={p.id} style={{ breakInside: 'avoid', padding: '3px 0' }}><Link to={`/concept/${p.id}`}>{c && <Dot status={c.status} />} {p.name}</Link></li>
            })}
          </ol>
        </div>
      )}
      {tab === '错题本' && (d.mistakes.length ? (
        <div className="panel table-wrap" style={{ padding: 0 }}>
          <table className="data">
            <thead><tr><th>知识点</th><th>题目</th><th>你的回答</th><th>反馈</th></tr></thead>
            <tbody>{d.mistakes.map(m => (
              <tr key={m.id}><td>{m.concept}<div className="faint">{LEVEL_LABEL[m.level]}题，{ERR_LABEL[m.error_type] ?? m.error_type}</div></td>
                <td>{m.prompt}</td><td className="muted">{m.response}</td><td className="muted">{m.feedback}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : <div className="empty">还没有错题。得分低于 70% 的回答会收进这里。</div>)}
      {tab === '个人笔记' && <div className="empty"><b>开发中（Phase 4）。</b>你可以在书、章节和知识点上写自己的笔记和观点，它们会进入你的个人知识图谱。</div>}
      {tab === '章节联系' && <BookRelations bookId={d.book.id} />}
    </div>
  )
}
