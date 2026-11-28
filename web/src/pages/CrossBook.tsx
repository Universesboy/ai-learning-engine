import { useState } from 'react'
import { api, post } from '../api'
import { useApi } from '../hooks'
import { ErrorBox, Skeleton } from '../components/ui'

interface Passage { id: string; chunk_id: number; book_id: number; book: string; author: string | null; chapter: string | null; page: number; text: string; concept?: string }
interface Item { text: string; cites: string[]; quote: string; verified: boolean }
interface Answer { question: string; passages: Passage[]; by_book: { book: string; views: Item[] }[]; synthesis: Item[]; gaps: string; stats: { views: number; verified: number } }
interface Book { id: number; title: string; author: string | null; concepts: number }

const EXAMPLES = ['比较这些书里关于风险的不同理解', '哪些观点可以相互补充，哪些存在冲突？', '直觉判断什么时候可靠？']

function Cite({ id, p, open, onOpen }: { id: string; p?: Passage; open: boolean; onOpen: () => void }) {
  if (!p) return null
  return <button className={`cite${open ? ' on' : ''}`} onClick={onOpen} title={`《${p.book}》p.${p.page}`}>{p.book.length > 8 ? p.book.slice(0, 7) + '…' : p.book} p.{p.page}</button>
}

function ItemView({ it, k, by, open, setOpen }: { it: Item; k: string; by: Map<string, Passage>; open: string | null; setOpen: (s: string | null) => void }) {
  const mine = open?.startsWith(k + '|') ? open.slice(k.length + 1) : null
  return (
    <li>
      {it.text}{' '}
      {it.verified ? it.cites.map(c => <Cite key={c} id={c} p={by.get(c)} open={mine === c} onOpen={() => setOpen(mine === c ? null : `${k}|${c}`)} />)
        : <span className="pill st-shaky" title="引用核对不上原文，已去掉引用">证据不足</span>}
      {mine && by.get(mine) && <PassageCard p={by.get(mine)!} quote={it.quote} />}
    </li>
  )
}

function PassageCard({ p, quote }: { p: Passage; quote?: string }) {
  const [full, setFull] = useState(false)
  const at = quote ? p.text.indexOf(quote) : -1
  const head = at >= 0 && !full ? Math.max(0, at - 160) : 0
  const tail = at >= 0 && !full ? Math.min(p.text.length, at + quote!.length + 160) : p.text.length
  return (
    <div className="passage fade-in">
      <p className="faint">《{p.book}》{p.author ? ` ${p.author}` : ''}，{p.chapter}，第 {p.page} 页{p.concept ? `，相关知识点：${p.concept}` : ''}</p>
      <p>{head > 0 && '…'}{at >= 0 ? <>{p.text.slice(head, at)}<mark>{quote}</mark>{p.text.slice(at + quote!.length, tail)}</> : p.text.slice(head, tail)}{tail < p.text.length && '…'}</p>
      {at >= 0 && (head > 0 || tail < p.text.length) && <button className="btn ghost small" onClick={() => setFull(!full)}>{full ? '只看引用附近' : '展开这一段'}</button>}
    </div>
  )
}

export default function CrossBook() {
  const lib = useApi<Book[]>('/library')
  const hist = useApi<{ id: number; question: string; created_at: string }[]>('/crossbook')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Set<number> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [ans, setAns] = useState<Answer | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  if (lib.error) return <ErrorBox message={lib.error} retry={lib.reload} />
  if (!lib.data) return <Skeleton h={300} />
  const books = lib.data.filter(b => b.concepts > 0)
  const chosen = sel ?? new Set(books.map(b => b.id))
  const ask = async (question = q) => {
    if (question.trim().length < 2) return
    setBusy(true); setErr(null); setOpen(null); setShowAll(false)
    try { setAns(await post<Answer>('/crossbook', { question, book_ids: [...chosen] })); hist.reload() }
    catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const by = new Map(ans?.passages.map(p => [p.id, p]) ?? [])

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h1>跨书学习</h1>
          <p className="muted">向你的整个知识库提问。回答先分别写每位作者的原观点，再写 AI 的归纳；每条都标注书名、章节和页码，引用由程序核对原文。</p>
        </div>
      </div>
      <div className="cross">
        <div className="stack">
          <section className="panel">
            <textarea className="textarea" value={q} onChange={e => setQ(e.target.value)} placeholder="例如：比较这几本书对风险的理解有什么不同"
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask() }} aria-label="问题" />
            {books.length > 1 && (
              <div className="graph-filters" style={{ marginTop: 10 }}>
                {books.map(b => (
                  <button key={b.id} className="chip" aria-pressed={chosen.has(b.id)}
                    onClick={() => { const n = new Set(chosen); n.has(b.id) ? n.delete(b.id) : n.add(b.id); setSel(n) }}>
                    {b.title.length > 16 ? b.title.slice(0, 15) + '…' : b.title}</button>
                ))}
              </div>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={busy || q.trim().length < 2 || !chosen.size} onClick={() => ask()}>{busy ? '正在查找原文并比较……' : '提问'}</button>
              <span className="faint">每个问题约 $0.03（Claude Sonnet），Ctrl + Enter 发送</span>
            </div>
            {!ans && !busy && <div className="row" style={{ marginTop: 10 }}>{EXAMPLES.map(e => <button key={e} className="chip" onClick={() => { setQ(e); ask(e) }}>{e}</button>)}</div>}
            {books.length < 2 && <p className="faint" style={{ marginTop: 8 }}>现在只有 {books.length} 本书整理了知识点，回答只会来自这一本。整理更多的书后就能比较不同作者。</p>}
            {err && <p className="error">{err}</p>}
          </section>

          {busy && <Skeleton h={260} />}
          {ans && !busy && (
            <section className="panel answer fade-in">
              <h2>{ans.question}</h2>
              <p className="faint">{ans.stats.verified} / {ans.stats.views} 条论述的引用已核对原文。</p>
              {ans.by_book.map(b => (
                <div key={b.book} className="answer-book">
                  <h3>《{b.book}》作者原观点</h3>
                  <ul>{b.views.map((v, i) => <ItemView key={i} k={`${b.book}-${i}`} it={v} by={by} open={open} setOpen={setOpen} />)}</ul>
                </div>
              ))}
              {ans.synthesis.length > 0 && (
                <div className="answer-ai">
                  <h3>AI 归纳</h3>
                  <p className="faint">以下是 AI 根据上面的原文做的比较和归纳，不是书中原话。</p>
                  <ul>{ans.synthesis.map((v, i) => <ItemView key={i} k={`ai-${i}`} it={v} by={by} open={open} setOpen={setOpen} />)}</ul>
                </div>
              )}
              {ans.gaps && <p className="faint" style={{ marginTop: 12 }}>原文没有覆盖的部分：{ans.gaps}</p>}
              <button className="btn ghost small" style={{ marginTop: 10 }} onClick={() => setShowAll(!showAll)}>{showAll ? '收起原文' : `查看检索到的 ${ans.passages.length} 段原文`}</button>
              {showAll && ans.passages.map(p => <PassageCard key={p.id} p={p} />)}
            </section>
          )}
        </div>
        <aside className="stack">
          <section className="panel">
            <h3>提问记录</h3>
            {hist.data?.length ? <div className="list" style={{ marginTop: 6 }}>{hist.data.map(h => (
              <button key={h.id} className="list-item as-button" onClick={async () => { setAns(await api<Answer>(`/crossbook/${h.id}`)); setQ(h.question); setOpen(null) }}>
                <span className="name">{h.question}</span></button>
            ))}</div> : <p className="faint" style={{ marginTop: 6 }}>还没有提过问题。</p>}
          </section>
        </aside>
      </div>
    </div>
  )
}
