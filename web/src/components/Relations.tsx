import { Link } from 'react-router-dom'

export interface Relation {
  id: number; type: string; label: string; source_id: number; target_id: number; source_name: string; target_name: string
  source_book_title: string; target_book_title: string; source_author: string | null; target_author: string | null
  source_chapter: string | null; target_chapter: string | null; rationale: string; confidence: number; cross_book: boolean
  source_quote: string; source_page: number; target_quote: string; target_page: number
}
export const REL_LABEL: Record<string, string> = {
  similar_to: '相似概念', supports: '相互支持', contradicts: '观点冲突', extends: '理论延伸',
  prerequisite: '前置知识', applies_to: '实际应用', example_of: '案例说明',
}
export const REL_VAR: Record<string, string> = {
  similar_to: '--r-similar', supports: '--r-supports', contradicts: '--r-contradicts', extends: '--r-extends',
  prerequisite: '--r-prereq', applies_to: '--r-applies', example_of: '--r-example',
}
export function RelationCard({ r, self }: { r: Relation; self: number }) {
  if (self === -1) return <PairCard r={r} />
  const outgoing = r.source_id === self
  const other = outgoing ? { id: r.target_id, name: r.target_name, book: r.target_book_title, author: r.target_author, chapter: r.target_chapter }
    : { id: r.source_id, name: r.source_name, book: r.source_book_title, author: r.source_author, chapter: r.source_chapter }
  return (
    <div className="rel-card">
      <p><span className="rel-type" style={{ ['--c' as any]: `var(${REL_VAR[r.type]})` }}>{r.label}</span>
        {outgoing ? '→ ' : '← '}<Link to={`/concept/${other.id}`} style={{ fontWeight: 600 }}>{other.name}</Link>
        {r.cross_book && <span className="faint">　跨书</span>}</p>
      <p className="faint">《{other.book}》{other.author ? ` ${other.author}` : ''}，{other.chapter}</p>
      <p style={{ marginTop: 6 }}>{r.rationale}</p>
      <blockquote>“{r.source_quote}”<span className="faint">　{r.source_name}，p.{r.source_page}</span></blockquote>
      <blockquote>“{r.target_quote}”<span className="faint">　{r.target_name}，p.{r.target_page}</span></blockquote>
      <p className="faint">AI 根据两段原文判断，可信度 {Math.round(r.confidence * 100)}%，两处引用都已核对原文。</p>
    </div>
  )
}


/** Both sides shown (book page): source —type→ target, with the two quotes. */
function PairCard({ r }: { r: Relation }) {
  return (
    <div className="rel-card">
      <p><Link to={`/concept/${r.source_id}`} style={{ fontWeight: 600 }}>{r.source_name}</Link>{' '}
        <span className="rel-type" style={{ ['--c' as any]: `var(${REL_VAR[r.type]})` }}>{r.label}</span>{' '}
        <Link to={`/concept/${r.target_id}`} style={{ fontWeight: 600 }}>{r.target_name}</Link></p>
      <p className="faint">{r.cross_book ? `《${r.source_book_title}》${r.source_chapter ?? ''} ↔ 《${r.target_book_title}》${r.target_chapter ?? ''}`
        : `${r.source_chapter ?? ''} ↔ ${r.target_chapter ?? ''}`}</p>
      <p style={{ marginTop: 6 }}>{r.rationale}</p>
      <blockquote>“{r.source_quote}”<span className="faint">　p.{r.source_page}</span></blockquote>
      <blockquote>“{r.target_quote}”<span className="faint">　p.{r.target_page}</span></blockquote>
      <p className="faint">可信度 {Math.round(r.confidence * 100)}%</p>
    </div>
  )
}
