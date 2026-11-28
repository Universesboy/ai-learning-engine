import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Graphology from 'graphology'
import Sigma from 'sigma'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import { api, Status, STATUS_LABEL, STATUS_ORDER } from '../api'
import { cssVar, useApi, useIsDark } from '../hooks'
import { Dot, ErrorBox, Skeleton } from '../components/ui'
import { LinkPanel } from '../components/LinkPanel'
import { REL_LABEL, REL_VAR, RelationCard, Relation } from '../components/Relations'

interface GraphData {
  books: { id: number; title: string; author: string | null }[]
  chapters: { id: number; book_id: number; ord: number; title: string; concepts: number; summary: string | null }[]
  concepts: [number, number, number, string, Status][]      // id, chapter, book, name, status
  relations: [number, number, number, string, number][]     // id, source, target, type, confidence
  prerequisites: [number, number][]
}
interface ConceptDetail { id: number; name: string; summary: string; pages: string; book_title: string; chapter_title: string; relations: Relation[] }

const STATUS_VAR: Record<Status, string> = {
  not_started: '--m-none', needs_relearning: '--m-relearn', shaky: '--m-shaky', cannot_apply_yet: '--m-apply', mastered: '--m-mastered',
}
const BOOK_VARS = ['--b1', '--b2', '--b3', '--b4', '--b5', '--b6']

type View = { level: 'topic' } | { level: 'chapter'; chapterId: number }

function buildGraph(d: GraphData, view: View, types: Set<string>, books: Set<number>, status: Status | 'all', showMap: boolean) {
  const g = new Graphology({ type: 'directed', multi: true })
  const chapterOf = new Map(d.concepts.map(c => [c[0], c]))
  const bookColor = (b: number) => cssVar(BOOK_VARS[d.books.findIndex(x => x.id === b) % BOOK_VARS.length])
  const rels = d.relations.filter(r => types.has(r[3]) && books.has(chapterOf.get(r[1])?.[2] ?? -1) && books.has(chapterOf.get(r[2])?.[2] ?? -1))
  if (view.level === 'topic') {
    const chs = d.chapters.filter(c => books.has(c.book_id))
    const byBook = new Map<number, typeof chs>()
    chs.forEach(c => byBook.set(c.book_id, [...(byBook.get(c.book_id) ?? []), c]))
    const nb = byBook.size || 1
    let bi = 0
    byBook.forEach((list, bid) => {  // each book starts on its own circle so books stay apart
      const cx = Math.cos((2 * Math.PI * bi) / nb) * 60, cy = Math.sin((2 * Math.PI * bi) / nb) * 60
      list.forEach((c, i) => g.addNode(`ch${c.id}`, {
        x: cx + Math.cos((2 * Math.PI * i) / list.length) * 20, y: cy + Math.sin((2 * Math.PI * i) / list.length) * 20,
        size: 4 + Math.sqrt(c.concepts) * 1.6, label: c.title.length > 18 ? c.title.slice(0, 17) + '…' : c.title,
        color: bookColor(bid), kind: 'chapter', ref: c.id,
      }))
      bi++
    })
    const agg = new Map<string, { n: number; cross: boolean }>()
    rels.forEach(r => {
      const a = chapterOf.get(r[1]), b = chapterOf.get(r[2])
      if (!a || !b || a[1] === b[1]) return
      const k = [a[1], b[1]].sort((x, y) => x - y).join('-')
      const cur = agg.get(k) ?? { n: 0, cross: a[2] !== b[2] }
      agg.set(k, { ...cur, n: cur.n + 1 })
    })
    agg.forEach((v, k) => {
      const [a, b] = k.split('-')
      if (g.hasNode(`ch${a}`) && g.hasNode(`ch${b}`))
        g.addEdgeWithKey(k, `ch${a}`, `ch${b}`, { size: Math.min(6, 0.6 + v.n * 0.5), color: v.cross ? cssVar('--iris') : cssVar('--line-strong'), n: v.n })
    })
  } else {
    const focus = new Set(d.concepts.filter(c => c[1] === view.chapterId).map(c => c[0]))
    const shown = new Set(focus)
    rels.forEach(r => { if (focus.has(r[1])) shown.add(r[2]); if (focus.has(r[2])) shown.add(r[1]) })
    d.concepts.filter(c => shown.has(c[0]) && (status === 'all' || c[4] === status || !focus.has(c[0]))).forEach((c, i) => g.addNode(`c${c[0]}`, {
      x: Math.cos(i) * (focus.has(c[0]) ? 10 : 30), y: Math.sin(i) * (focus.has(c[0]) ? 10 : 30),
      size: focus.has(c[0]) ? 7 : 5, label: c[3], color: cssVar(STATUS_VAR[c[4]]), kind: 'concept', ref: c[0],
      borderColor: bookColor(c[2]), outside: !focus.has(c[0]),
    }))
    rels.forEach(r => {
      if (g.hasNode(`c${r[1]}`) && g.hasNode(`c${r[2]}`))
        g.addEdgeWithKey(`r${r[0]}`, `c${r[1]}`, `c${r[2]}`, { size: 1 + r[4] * 2, color: cssVar(REL_VAR[r[3]]), type: 'arrow', rel: r[3] })
    })
    if (showMap) d.prerequisites.forEach(([s, t], i) => {
      if (g.hasNode(`c${s}`) && g.hasNode(`c${t}`))
        g.addEdgeWithKey(`p${i}`, `c${t}`, `c${s}`, { size: 1, color: cssVar('--line-strong'), type: 'arrow', rel: 'map' })
    })
  }
  if (g.order > 1) forceAtlas2.assign(g, { iterations: 250, settings: { ...forceAtlas2.inferSettings(g), gravity: 1, scalingRatio: 8 } })
  return g
}

export default function GraphPage() {
  const { data, error, reload } = useApi<GraphData>('/graph')
  const dark = useIsDark()
  const box = useRef<HTMLDivElement>(null)
  const sigma = useRef<Sigma | null>(null)
  const [view, setView] = useState<View>({ level: 'topic' })
  const [types, setTypes] = useState<Set<string>>(new Set(Object.keys(REL_LABEL)))
  const [books, setBooks] = useState<Set<number> | null>(null)
  const [status, setStatus] = useState<Status | 'all'>('all')
  const [showMap, setShowMap] = useState(true)
  const [hover, setHover] = useState<string | null>(null)
  const [detail, setDetail] = useState<ConceptDetail | null>(null)
  const bookSet = useMemo(() => books ?? new Set(data?.books.map(b => b.id) ?? []), [books, data])

  useEffect(() => {
    if (!data || !box.current) return
    const g = buildGraph(data, view, types, bookSet, status, showMap)
    const r = new Sigma(g, box.current, {
      labelColor: { color: cssVar('--ink') }, labelFont: cssVar('--font') || 'sans-serif', labelSize: 12, labelWeight: '500',
      labelRenderedSizeThreshold: view.level === 'topic' ? 6 : 4, defaultEdgeType: 'line', minCameraRatio: 0.1, maxCameraRatio: 4,
      zIndex: true,
    })
    r.on('enterNode', ({ node }) => setHover(node))
    r.on('leaveNode', () => setHover(null))
    r.on('clickNode', ({ node }) => {
      const a = g.getNodeAttributes(node)
      if (a.kind === 'chapter') setView({ level: 'chapter', chapterId: a.ref })
      else api<ConceptDetail>(`/concepts/${a.ref}`).then(setDetail).catch(() => {})
    })
    sigma.current = r
    ;(box.current as any).__sigma = r  // lets browser tests find node positions
    return () => { r.kill(); sigma.current = null }
  }, [data, view, types, bookSet, status, showMap, dark])

  useEffect(() => {  // hover: keep the node and its neighbours, fade the rest
    const r = sigma.current
    if (!r) return
    const g = r.getGraph()
    const near = hover && g.hasNode(hover) ? new Set([hover, ...g.neighbors(hover)]) : null
    const faded = cssVar('--line')
    r.setSetting('nodeReducer', (n, a) => near && !near.has(n) ? { ...a, color: faded, label: '' } : { ...a, highlighted: n === hover })
    r.setSetting('edgeReducer', (e, a) => near && !(g.extremities(e).every(x => near.has(x))) ? { ...a, hidden: true } : a)
  }, [hover])

  if (error) return <ErrorBox message={error} retry={reload} />
  if (!data) return <Skeleton h={560} />
  const chapter = view.level === 'chapter' ? data.chapters.find(c => c.id === view.chapterId) : null
  const relCount = data.relations.length
  const crossCount = (() => {
    const b = new Map(data.concepts.map(c => [c[0], c[2]]))
    return data.relations.filter(r => b.get(r[1]) !== b.get(r[2])).length
  })()
  const toggle = <T,>(set: Set<T>, v: T) => { const n = new Set(set); n.has(v) ? n.delete(v) : n.add(v); return n }

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h1>知识图谱</h1>
          <p className="muted">{data.concepts.length} 个知识点，{relCount} 条有原文依据的关系，其中 {crossCount} 条跨书。每条关系都由 AI 读过双方原文判断，引用已核对。</p>
        </div>
      </div>

      <LinkPanel onDone={reload} />

      <section className="panel graph-panel">
        <div className="graph-tools">
          {view.level === 'chapter'
            ? <button className="btn small" onClick={() => { setView({ level: 'topic' }); setDetail(null) }}>返回主题概览</button>
            : <span className="faint">主题概览：每个点是一章，大小是知识点数量，颜色是书。连线是两章之间的关系数量，紫色为跨书。点击一章展开。</span>}
          {chapter && <b className="graph-title">{chapter.title}</b>}
        </div>
        <div className="graph-filters">
          {data.books.length > 1 && data.books.map((b, i) => (
            <button key={b.id} className="chip" aria-pressed={bookSet.has(b.id)} onClick={() => setBooks(toggle(bookSet, b.id))}>
              <span className="swatch" style={{ background: `var(${BOOK_VARS[i % BOOK_VARS.length]})` }} />{b.title.length > 14 ? b.title.slice(0, 13) + '…' : b.title}
            </button>
          ))}
          {Object.entries(REL_LABEL).map(([k, l]) => (
            <button key={k} className="chip" aria-pressed={types.has(k)} onClick={() => setTypes(toggle(types, k))}>
              <span className="swatch line" style={{ background: `var(${REL_VAR[k]})` }} />{l}
            </button>
          ))}
          {view.level === 'chapter' && <>
            <select className="select small" value={status} onChange={e => setStatus(e.target.value as Status | 'all')} aria-label="按掌握程度筛选">
              <option value="all">全部掌握程度</option>
              {STATUS_ORDER.map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            </select>
            <label className="check faint"><input type="checkbox" checked={showMap} onChange={e => setShowMap(e.target.checked)} />章内前置关系</label>
          </>}
        </div>
        <div className="graph-wrap">
          <div ref={box} className="graph-canvas" role="img" aria-label="知识图谱" />
          {detail && (
            <aside className="graph-detail fade-in">
              <button className="btn ghost small close" onClick={() => setDetail(null)} aria-label="关闭">×</button>
              <h3>{detail.name}</h3>
              <p className="faint">《{detail.book_title}》，{detail.chapter_title}，p.{JSON.parse(detail.pages || '[]').join(', ')}</p>
              <p style={{ marginTop: 8 }}>{detail.summary}</p>
              <p style={{ marginTop: 8 }}><Link className="btn small" to={`/concept/${detail.id}`}>去学习这个知识点</Link></p>
              <h4 style={{ marginTop: 14 }}>{detail.relations.length ? `${detail.relations.length} 条关系` : '还没有发现有依据的关系'}</h4>
              {detail.relations.map(r => <RelationCard key={r.id} r={r} self={detail.id} />)}
            </aside>
          )}
        </div>
        {view.level === 'chapter' && <p className="faint" style={{ marginTop: 8 }}>
          点的颜色是掌握程度（<Dot status="mastered" />已掌握 <Dot status="cannot_apply_yet" />还不会应用 <Dot status="shaky" />理解不牢 <Dot status="needs_relearning" />需要重学 <Dot status="not_started" />尚无测试），
          外圈是其他章节里与本章有关系的知识点。点击知识点看关系的原文依据。</p>}
      </section>
    </div>
  )
}
