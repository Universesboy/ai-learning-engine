import { Link } from 'react-router-dom'
import { fmtDateTime, pct } from '../api'
import { useApi } from '../hooks'
import { ErrorBox, Skeleton } from '../components/ui'

interface Due { concept_id: number; name: string; kind: string; book_id: number; due: string; retrievability: number }

export default function Review() {
  const { data, error, reload } = useApi<Due[]>('/review/due')
  if (error) return <ErrorBox message={error} retry={reload} />
  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h1>复习</h1>
          <p className="muted">按遗忘曲线到期的知识点，记住概率越低越靠前。每个知识点做一组练习就算复习一次。</p>
        </div>
      </div>
      {!data ? <Skeleton h={240} /> : data.length ? (
        <section className="panel">
          <div className="list">
            {[...data].sort((a, b) => a.retrievability - b.retrievability).map(d => (
              <Link key={d.concept_id} className="list-item" to={`/concept/${d.concept_id}?practice=1`}>
                <span className="num" style={{ width: 56, fontWeight: 650, color: d.retrievability < 0.8 ? 'var(--m-relearn)' : 'var(--m-shaky)' }}>{pct(d.retrievability)}</span>
                <span className="name">{d.name}</span>
                <span className="faint">到期 {fmtDateTime(d.due)}</span>
              </Link>
            ))}
          </div>
          <p className="faint" style={{ marginTop: 12 }}>左侧数字是现在还能记住的估计概率。一键连续刷题和按五个维度分流的复习在 Phase 5 加入。</p>
        </section>
      ) : (
        <div className="empty">现在没有到期的知识点。做过练习的知识点会按遗忘曲线自动回到这里。</div>
      )}
    </div>
  )
}
