import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ERR_LABEL, LEVEL_LABEL, QTYPE_LABEL, Status, STATUS_LABEL, fmtDateTime, post, pct } from '../api'
import { useApi } from '../hooks'
import { ErrorBox, Pill, Skeleton } from '../components/ui'
import { Markdown } from '../components/Markdown'
import { RelationCard, Relation } from '../components/Relations'

interface ConceptData {
  id: number; book_id: number; chapter_id: number; name: string; summary: string; kind: string; form: string
  pages: string; examples: string; confusions: string; formulas: string; book_title: string; chapter_title: string
  progress: { mastery: number; can_apply: boolean; status: Status }
  artifacts: { id: number; kind: string }[]
  requires: { id: number; name: string }[]; required_by: { id: number; name: string }[]
  attempts: { created_at: string; score: number; error_type: string; level: string; prompt: string }[]
  relations: Relation[]
}
interface Turn { role: 'teacher' | 'me'; text: string; question?: string }
interface Question { id: number; qtype: string; level: string; prompt: string; choices: string[] | null; pages: number[] }
interface Graded { score: number; error_type: string; feedback: string; next_review: string; status: { status: Status; mastery: number } }

const KIND: Record<string, string> = { memory: '需要记住的事实', concept: '需要理解的概念', procedure: '需要会做的步骤', design: '需要会分析运用' }
const TOOL: Record<string, string> = { explain: '讲解', interactive_sim: '交互演示', walkthrough: '分步演示', manim: '动画', diagram: '关系图', timeline: '时间线', code_trace: '代码追踪', case_study: '案例分析', flashcards: '记忆卡片', story: '故事化' }
const parse = (s: string) => { try { return JSON.parse(s) ?? [] } catch { return [] } }
const bold = (s: string) => s.split(/\*\*(.+?)\*\*/g).map((p, i) => i % 2 ? <b key={i}>{p}</b> : p)

function QuestionCard({ q, onGraded }: { q: Question; onGraded: () => void }) {
  const [resp, setResp] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<Graded | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const submit = async () => {
    if (!resp.trim()) return
    setBusy(true); setErr(null)
    try { setRes(await post<Graded>(`/questions/${q.id}/answer`, { response: resp })); onGraded() } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const choices = q.choices && q.choices.length ? q.choices : q.qtype === 'true_false' ? ['对', '错'] : null
  return (
    <div className="panel q-card">
      <p className="faint">{LEVEL_LABEL[q.level] ?? q.level}，{QTYPE_LABEL[q.qtype] ?? q.qtype}</p>
      <p style={{ marginTop: 6, fontWeight: 550 }}>{bold(q.prompt)}</p>
      {choices ? choices.map(c => (
        <label key={c} className="opt"><input type="radio" name={`q${q.id}`} disabled={!!res}
          value={q.qtype === 'true_false' ? (c === '对' ? 'true' : 'false') : c} onChange={e => setResp(e.target.value)} />{c}</label>
      )) : (
        <textarea className="textarea" style={{ marginTop: 10, minHeight: q.qtype === 'fill_in' ? 44 : 110 }} disabled={!!res}
          value={resp} onChange={e => setResp(e.target.value)} placeholder="用你自己的话回答" />
      )}
      {!res && <div className="row" style={{ marginTop: 10 }}><button className="btn primary small" disabled={busy || !resp.trim()} onClick={submit}>{busy ? '判分中……' : '提交回答'}</button></div>}
      {err && <p className="error">{err}</p>}
      {res && (
        <div className="feedback fade-in">
          <p><b className="num" style={{ color: res.score >= 0.7 ? 'var(--m-mastered)' : 'var(--m-relearn)' }}>{pct(res.score)}</b>
            {res.error_type !== 'none' && <span className="muted">　错因：{ERR_LABEL[res.error_type] ?? res.error_type}</span>}</p>
          <div style={{ marginTop: 6 }}><Markdown text={res.feedback} /></div>
          <p className="faint" style={{ marginTop: 6 }}>现在的状态：{STATUS_LABEL[res.status.status]}，下次复习 {res.next_review.slice(0, 10)}</p>
        </div>
      )}
    </div>
  )
}

export default function ConceptPage() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const { data: c, error, reload } = useApi<ConceptData>(`/concepts/${id}`)
  const [session, setSession] = useState<number | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [tools, setTools] = useState<string[]>([])
  const [msg, setMsg] = useState('')
  const [questions, setQuestions] = useState<Question[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [visuals, setVisuals] = useState<{ id: number; kind: string }[]>([])

  useEffect(() => { setSession(null); setTurns([]); setQuestions(null); setErr(null) }, [id])
  useEffect(() => { if (c) setVisuals(c.artifacts) }, [c])

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setErr(null)
    try { await fn() } catch (e: any) { setErr(e.message) } finally { setBusy(null) }
  }
  const startLesson = () => run('lesson', async () => {
    const r = await post<any>(`/concepts/${id}/lesson`)
    setSession(r.session_id); setTools(r.tools)
    setTurns([{ role: 'teacher', text: r.explanation, question: r.check_question }])
  })
  const reply = () => run('reply', async () => {
    const text = msg.trim(); if (!text || !session) return
    setMsg(''); setTurns(t => [...t, { role: 'me', text }])
    const r = await post<any>(`/sessions/${session}/reply`, { message: text })
    setTurns(t => [...t, { role: 'teacher', text: r.reply, question: r.next_question }])
  })
  const loadQuiz = () => run('quiz', async () => { setQuestions(await post<Question[]>(`/concepts/${id}/questions`)) })
  const makeVisual = (kind: string) => run(kind, async () => {
    const r = await post<any>(`/concepts/${id}/visual?kind=${kind}`)
    setVisuals(v => [{ id: r.artifact_id, kind }, ...v])
  })
  useEffect(() => { if (params.get('practice') && c && !questions) loadQuiz() }, [c]) // eslint-disable-line

  if (error) return <ErrorBox message={error} retry={reload} />
  if (!c) return <Skeleton h={400} />
  const pages: number[] = parse(c.pages), examples: string[] = parse(c.examples), confusions: string[] = parse(c.confusions)

  return (
    <div className="fade-in">
      <p style={{ marginBottom: 16 }} className="faint">
        <Link to={`/library/${c.book_id}`}>{c.book_title}</Link>，{c.chapter_title}
      </p>
      <div className="page-head">
        <div style={{ maxWidth: 760 }}>
          <div className="row"><h1>{c.name}</h1><Pill status={c.progress.status} /></div>
          <p className="muted" style={{ marginTop: 10 }}>{c.summary}</p>
          <p className="faint" style={{ marginTop: 6 }}>原文页码 p.{pages.join(', ')}，{KIND[c.kind] ?? c.kind}</p>
        </div>
      </div>

      <div className="learn">
        <div className="stack">
          <div className="row">
            <button className="btn primary" disabled={!!busy} onClick={startLesson}>{busy === 'lesson' ? '老师正在备课……' : session ? '重新讲解' : '开始讲解'}</button>
            <button className="btn" disabled={!!busy} onClick={loadQuiz}>{busy === 'quiz' ? '正在出题……' : '练习测验'}</button>
            <button className="btn" disabled={!!busy} onClick={() => makeVisual('interactive_sim')}>{busy === 'interactive_sim' ? '生成中，约 30 秒……' : '生成交互演示'}</button>
          </div>
          {err && <p className="error">{err}</p>}

          {turns.length > 0 && (
            <section className="panel">
              {tools.length > 0 && <p className="faint" style={{ marginBottom: 10 }}>推荐的学习方式：{tools.map(t => TOOL[t] ?? t).join('，')}</p>}
              <div className="chat">
                {turns.map((t, i) => (
                  <div key={i} className={`msg${t.role === 'me' ? ' me' : ''}`}>
                    <Markdown text={t.text} />{t.question && <span className="q"><Markdown text={t.question} /></span>}
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 12 }}>
                <textarea className="textarea" value={msg} onChange={e => setMsg(e.target.value)} placeholder="写下你的回答或追问。试着用自己的话解释，而不是复述原文。"
                  onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) reply() }} />
                <div className="row" style={{ marginTop: 8 }}><button className="btn primary small" disabled={!!busy || !msg.trim()} onClick={reply}>{busy === 'reply' ? '老师在看你的回答……' : '发送'}</button><span className="faint">Ctrl + Enter 发送</span></div>
              </div>
            </section>
          )}

          {questions && (
            <section className="stack">
              <h2>练习</h2>
              {questions.length ? questions.map(q => <QuestionCard key={q.id} q={q} onGraded={reload} />) : <div className="empty">没有生成题目，请重试。</div>}
            </section>
          )}

          {visuals.map(v => v.kind === 'manim'
            ? <video key={v.id} controls src={`/api/artifacts/${v.id}`} style={{ width: '100%' }} />
            : <iframe key={v.id} className="visual" src={`/api/artifacts/${v.id}`} title={`${c.name} 交互演示`} />)}

          {!turns.length && !questions && !visuals.length && (
            <div className="empty">建议顺序：先听讲解并回答老师的问题，再做练习。掌握需要通过应用题，只答对记忆题不算掌握。</div>
          )}
        </div>

        <aside className="stack">
          <section className="panel">
            <h3>掌握情况</h3>
            <p style={{ marginTop: 8 }}><b className="num" style={{ fontSize: 22 }}>{c.progress.status === 'not_started' ? '—' : pct(c.progress.mastery)}</b>
              <span className="faint">　{c.progress.can_apply ? '已通过应用题' : '还没有通过应用题'}</span></p>
            <p className="faint" style={{ marginTop: 6 }}>理解、记忆、解释、推导、应用五个维度的独立评分在 Phase 5 加入。</p>
            {c.attempts.length > 0 && (
              <div className="list" style={{ marginTop: 10 }}>
                {c.attempts.slice(0, 8).map((a, i) => (
                  <div key={i} className="list-item">
                    <span className="num" style={{ width: 40, fontWeight: 650, color: a.score >= 0.7 ? 'var(--m-mastered)' : 'var(--m-relearn)' }}>{pct(a.score)}</span>
                    <span className="name faint">{LEVEL_LABEL[a.level]}题</span><span className="faint">{fmtDateTime(a.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
          {(c.requires.length > 0 || c.required_by.length > 0) && (
            <section className="panel">
              {c.requires.length > 0 && <><h3>先要理解</h3><div className="list">{c.requires.map(r => <Link key={r.id} className="list-item" to={`/concept/${r.id}`}><span className="name">{r.name}</span></Link>)}</div></>}
              {c.required_by.length > 0 && <><h3 style={{ marginTop: c.requires.length ? 14 : 0 }}>学会后可以学</h3><div className="list">{c.required_by.map(r => <Link key={r.id} className="list-item" to={`/concept/${r.id}`}><span className="name">{r.name}</span></Link>)}</div></>}
            </section>
          )}
          {c.relations?.length > 0 && (
            <section className="panel">
              <h3>知识关联</h3>
              <p className="faint">AI 读过双方原文判断的关系，两处引用都核对过。</p>
              {c.relations.slice(0, 8).map(r => <RelationCard key={r.id} r={r} self={c.id} />)}
              {c.relations.length > 8 && <Link className="faint" to="/graph">还有 {c.relations.length - 8} 条，在知识图谱里查看</Link>}
            </section>
          )}
          {examples.length > 0 && <section className="panel"><h3>书中的例子</h3><ul className="soon-list">{examples.map((e, i) => <li key={i}>{e}</li>)}</ul></section>}
          {confusions.length > 0 && <section className="panel"><h3>容易混淆</h3><ul className="soon-list">{confusions.map((e, i) => <li key={i}>{e}</li>)}</ul></section>}
        </aside>
      </div>
    </div>
  )
}
