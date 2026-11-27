import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Status, STATUS_LABEL } from '../api'
import { cssVar, useIsDark } from '../hooks'

export interface FieldData {
  books: { id: number; title: string }[]
  chapters: { id: number; book_id: number; ord: number; title: string }[]
  concepts: [number, number, string, Status, number | null][] // id, chapter_id, name, status, retrievability
}

const DOT = 9, GAP = 4, COLS = 4, CH_GAP = 14, ROW_GAP = 18, PAD = 16, BOOK_HEAD = 26

const VAR: Record<Status, string> = {
  not_started: '--m-none', needs_relearning: '--m-relearn', shaky: '--m-shaky', cannot_apply_yet: '--m-apply', mastered: '--m-mastered',
}

interface Placed { x: number; y: number; id: number; name: string; status: Status; r: number | null; chapter: string }

/** Every concept in the library as one dot, grouped book > chapter. Colour = mastery state;
 *  a reviewed concept fades as its recall probability drops, so forgetting is visible. */
export function MasteryField({ data }: { data: FieldData }) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [width, setWidth] = useState(0)
  const [tip, setTip] = useState<{ x: number; y: number; p: Placed } | null>(null)
  const dark = useIsDark()
  const nav = useNavigate()

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(e => setWidth(Math.floor(e[0].contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const layout = useMemo(() => {
    const placed: Placed[] = []
    const labels: { x: number; y: number; text: string }[] = []
    if (!width) return { placed, labels, height: 0 }
    const byChapter = new Map<number, FieldData['concepts']>()
    for (const c of data.concepts) {
      const list = byChapter.get(c[1]) ?? []
      list.push(c)
      byChapter.set(c[1], list)
    }
    const blockW = COLS * (DOT + GAP) - GAP
    let y = PAD
    for (const book of data.books) {
      const chs = data.chapters.filter(ch => ch.book_id === book.id && byChapter.has(ch.id))
      if (!chs.length) continue
      if (data.books.length > 1) { labels.push({ x: PAD, y: y + 12, text: book.title }); y += BOOK_HEAD }
      let x = PAD, rowH = 0
      for (const ch of chs) {
        const items = byChapter.get(ch.id)!
        const rows = Math.ceil(items.length / COLS)
        const h = rows * (DOT + GAP) - GAP
        if (x + blockW > width - PAD) { x = PAD; y += rowH + ROW_GAP; rowH = 0 }
        items.forEach((c, i) => placed.push({
          x: x + (i % COLS) * (DOT + GAP) + DOT / 2, y: y + Math.floor(i / COLS) * (DOT + GAP) + DOT / 2,
          id: c[0], name: c[2], status: c[3], r: c[4], chapter: ch.title,
        }))
        x += blockW + CH_GAP
        rowH = Math.max(rowH, h)
      }
      y += rowH + ROW_GAP + 6
    }
    return { placed, labels, height: y - ROW_GAP + PAD }
  }, [data, width])

  useEffect(() => {
    const cv = canvas.current
    if (!cv || !width) return
    const dpr = window.devicePixelRatio || 1
    cv.width = width * dpr; cv.height = layout.height * dpr
    cv.style.height = `${layout.height}px`
    const ctx = cv.getContext('2d')!
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, width, layout.height)
    const colors = Object.fromEntries(Object.entries(VAR).map(([k, v]) => [k, cssVar(v)])) as Record<Status, string>
    ctx.font = `600 12px ${cssVar('--font')}`
    ctx.fillStyle = cssVar('--ink-2')
    for (const l of layout.labels) ctx.fillText(l.text, l.x, l.y)
    for (const p of layout.placed) {
      ctx.beginPath()
      ctx.arc(p.x, p.y, DOT / 2, 0, Math.PI * 2)
      if (p.status === 'not_started') {
        ctx.strokeStyle = colors.not_started; ctx.lineWidth = 1.2; ctx.stroke()
      } else {
        ctx.globalAlpha = p.r == null ? 1 : 0.35 + 0.65 * p.r
        ctx.fillStyle = colors[p.status]; ctx.fill()
        ctx.globalAlpha = 1
      }
    }
  }, [layout, width, dark])

  const hit = (e: React.MouseEvent) => {
    const rect = canvas.current!.getBoundingClientRect()
    const x = e.clientX - rect.left, y = e.clientY - rect.top
    let best: Placed | null = null, bd = 64
    for (const p of layout.placed) {
      const d = (p.x - x) ** 2 + (p.y - y) ** 2
      if (d < bd) { bd = d; best = p }
    }
    return best ? { p: best, x, y } : null
  }

  return (
    <div className="field-wrap" ref={wrap}>
      <canvas
        ref={canvas} className="field-canvas" role="img"
        aria-label={`知识库中的 ${data.concepts.length} 个知识点，按书和章节排列，颜色表示掌握状态`}
        onMouseMove={e => setTip(hit(e))} onMouseLeave={() => setTip(null)}
        onClick={e => { const h = hit(e); if (h) nav(`/concept/${h.p.id}`) }}
      />
      {tip && (
        <div className="field-tip" style={{ left: Math.min(tip.x + 14, width - 270), top: tip.y + 14 }}>
          <b>{tip.p.name}</b>
          <div className="faint">{tip.p.chapter}</div>
          <div className="faint">{STATUS_LABEL[tip.p.status]}{tip.p.r != null && `，当前记住的概率约 ${Math.round(tip.p.r * 100)}%`}</div>
        </div>
      )}
    </div>
  )
}
