import { lazy, Suspense } from 'react'
import { Link } from 'react-router-dom'
import { BookSummary, ERR_LABEL, LEVEL_LABEL, Status, fmtDate, fmtDateTime, pct } from '../api'
import { useApi } from '../hooks'
import { Cover, Dot, ErrorBox, Legend, Pill, RepairBanner, Skeleton, StatusMeter } from '../components/ui'
import type { FieldData } from '../components/MasteryField'

const MasteryField = lazy(() => import('../components/MasteryField').then(m => ({ default: m.MasteryField })))
const TrendChart = lazy(() => import('../components/TrendChart').then(m => ({ default: m.TrendChart })))

interface OverviewData {
  kpis: { due_today: number; books: number; books_learning: number; concepts: number; mastered: number; mastery_rate: number
    with_evidence: number; attempts: number; mastered_change: number | null }
  status_counts: Partial<Record<Status, number>>
  due: { id: number; name: string; book: string; due: string; forgetting_risk: number | null; status: Status }[]
  weak: { id: number; name: string; book: string; status: Status; mastery: number; can_apply: boolean }[]
  recent: { id: number; created_at: string; score: number; error_type: string; level: string; concept_id: number; concept: string; book: string }[]
  learning: BookSummary[]
  books: BookSummary[]
  trend: { day: string; mastered: number; evidence: number; attempts: number; avg_score: number | null }[]
  usage: { usd: number; calls: number }
  needs_repair: number
}

function headline(k: OverviewData['kpis']) {
  if (!k.books) return '先导入第一本书'
  if (k.due_today) return `今天有 ${k.due_today} 个知识点需要复习`
  if (!k.attempts) return `${k.concepts} 个知识点已经整理好，还没有开始测试`
  return '今天没有到期的复习'
}

export default function Overview() {
  const { data, error, reload } = useApi<OverviewData>('/overview')
  const field = useApi<FieldData>('/field')

  if (error) return <ErrorBox message={error} retry={reload} />
  if (!data) return <div className="stack"><Skeleton h={150} /><Skeleton h={320} /></div>
  const k = data.kpis

  return (
    <div className="fade-in">
      <RepairBanner onDone={() => { reload(); field.reload() }} />

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="today">
          <div className="today-text" style={{ flex: 1, minWidth: 240 }}>
            <h1>{headline(k)}</h1>
            <p className="muted" style={{ marginTop: 6 }}>
              {k.attempts
                ? `已测试 ${k.with_evidence} 个知识点，其中 ${k.mastered} 个达到掌握标准（需要通过应用题）。`
                : '掌握度只来自你的答题表现：读过不等于掌握。从一个知识点的讲解和练习开始。'}
            </p>
          </div>
          {k.due_today > 0
            ? <Link className="btn primary" to="/review">开始复习</Link>
            : data.learning[0]
              ? <Link className="btn primary" to={`/library/${data.learning[0].id}`}>继续学习</Link>
              : <Link className="btn primary" to="/library">{k.books ? '选择一个知识点开始' : '导入书籍'}</Link>}
        </div>
        <div className="figures">
          <div className="figure"><b className="num">{k.mastered}<span className="faint"> / {k.concepts}</span></b><span className="faint">已掌握的知识点</span></div>
          <div className="figure"><b className="num">{pct(k.mastery_rate)}</b><span className="faint">
            掌握率{k.mastered_change != null && k.mastered_change !== 0 ? `，近 7 天 ${k.mastered_change > 0 ? '+' : ''}${k.mastered_change}` : ''}</span></div>
          <div className="figure"><b className="num">{k.due_today}</b><span className="faint">今日待复习</span></div>
          <div className="figure"><b className="num">{k.books}</b><span className="faint">知识库中的书，{k.books_learning} 本在学</span></div>
        </div>
      </section>

      <div className="grid grid-overview">
        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>知识掌握地图</h2>
                <p className="faint" style={{ marginTop: 2 }}>每个点是一个知识点，按书和章节排列。复习过的知识点会随遗忘逐渐变淡。点击进入学习。</p>
              </div>
            </div>
            <StatusMeter counts={data.status_counts} total={k.concepts} />
            <div style={{ margin: '12px 0' }}><Legend /></div>
            {field.data ? <Suspense fallback={<Skeleton h={260} />}><MasteryField data={field.data} /></Suspense> : <Skeleton h={260} />}
          </section>

          {k.attempts > 0 && <section className="panel">
            <div className="panel-head"><h2>掌握趋势</h2></div>
            {data.trend.length >= 2
              ? <Suspense fallback={<Skeleton h={220} />}><TrendChart points={data.trend} /></Suspense>
              : <div className="empty">趋势需要至少两天的答题记录。已有 1 天，明天再做一次练习就能看到变化。</div>}
          </section>}

          <section className="panel">
            <div className="panel-head"><h2>最近答题</h2></div>
            {data.recent.length ? (
              <div className="list">
                {data.recent.map(r => (
                  <Link key={r.id} className="list-item" to={`/concept/${r.concept_id}`}>
                    <span className="num" style={{ width: 44, fontWeight: 650, color: r.score >= 0.7 ? 'var(--m-mastered)' : 'var(--m-relearn)' }}>{pct(r.score)}</span>
                    <span className="name">{r.concept}<span className="faint">　{LEVEL_LABEL[r.level] ?? r.level}题{r.error_type && r.error_type !== 'none' ? `，${ERR_LABEL[r.error_type] ?? r.error_type}` : ''}</span></span>
                    <span className="faint">{fmtDateTime(r.created_at)}</span>
                  </Link>
                ))}
              </div>
            ) : <div className="empty">还没有答题记录。打开任意知识点，点「练习测验」开始。</div>}
          </section>
        </div>

        <div className="stack">
          <section className="panel">
            <div className="panel-head"><h2>今日复习</h2>{data.due.length > 0 && <Link to="/review">全部</Link>}</div>
            {data.due.length ? (
              <div className="list">
                {data.due.map(d => (
                  <Link key={d.id} className="list-item" to={`/concept/${d.id}`}>
                    <Dot status={d.status} /><span className="name">{d.name}</span>
                    <span className="faint">{d.forgetting_risk != null ? `遗忘风险 ${pct(d.forgetting_risk)}` : fmtDate(d.due)}</span>
                  </Link>
                ))}
              </div>
            ) : <div className="empty">没有到期的知识点。做过练习的知识点会按遗忘曲线自动安排复习。</div>}
          </section>

          <section className="panel">
            <div className="panel-head"><h2>薄弱知识点</h2></div>
            {data.weak.length ? (
              <div className="list">
                {data.weak.map(w => (
                  <Link key={w.id} className="list-item" to={`/concept/${w.id}`}>
                    <span className="name">{w.name}</span><Pill status={w.status} />
                  </Link>
                ))}
              </div>
            ) : <div className="empty">还没有发现薄弱点。答错或只答对记忆题的知识点会出现在这里。</div>}
          </section>

          <section className="panel">
            <div className="panel-head"><h2>{data.learning.length ? '正在学习' : '我的书'}</h2><Link to="/library">知识库</Link></div>
            {(data.learning.length ? data.learning : data.books).map(b => (
              <Link key={b.id} to={`/library/${b.id}`} className="list-item">
                <Cover id={b.id} title={b.title} />
                <span className="name" style={{ whiteSpace: 'normal' }}>
                  <b>{b.title}</b>
                  <span className="faint" style={{ display: 'block' }}>{b.mastered} / {b.concepts} 已掌握，{b.last_studied ? `最近学习 ${fmtDate(b.last_studied)}` : '还没有开始测试'}</span>
                </span>
              </Link>
            ))}
            {!data.books.length && <div className="empty">知识库是空的。<Link to="/library" style={{ color: 'var(--iris)' }}>导入第一本书</Link></div>}
          </section>

          <section className="panel">
            <div className="panel-head"><h2>模型用量</h2></div>
            <p className="num"><b style={{ fontSize: 22 }}>${data.usage.usd.toFixed(2)}</b> <span className="faint">累计 {data.usage.calls} 次调用</span></p>
            <p className="faint" style={{ marginTop: 4 }}>按 Claude 官方价格估算，每本书的花费在书籍详情里。</p>
          </section>
        </div>
      </div>
    </div>
  )
}
