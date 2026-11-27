import { lazy, Suspense } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { BrandMark, ComingSoon, Icon, Skeleton } from './components/ui'
import { ThemePref, useTheme } from './hooks'
import Overview from './pages/Overview'
import Library from './pages/Library'

const BookDetail = lazy(() => import('./pages/BookDetail'))
const ConceptPage = lazy(() => import('./pages/ConceptPage'))
const Review = lazy(() => import('./pages/Review'))

const NAV = [
  { to: '/', label: '总览', icon: Icon.overview, end: true },
  { to: '/library', label: '知识库', icon: Icon.library },
  { to: '/review', label: '复习', icon: Icon.review },
  { to: '/graph', label: '知识图谱', icon: Icon.graph, soon: true },
  { to: '/cross-book', label: '跨书学习', icon: Icon.cross, soon: true, optional: true },
  { to: '/insights', label: '学习洞察', icon: Icon.insights, soon: true, optional: true },
]

function ThemeSwitch() {
  const [theme, setTheme] = useTheme()
  const opts: [ThemePref, string][] = [['system', '自动'], ['light', '浅色'], ['dark', '深色']]
  return (
    <div className="theme-switch" role="group" aria-label="主题">
      {opts.map(([v, l]) => <button key={v} aria-pressed={theme === v} onClick={() => setTheme(v)}>{l}</button>)}
    </div>
  )
}

export default function App() {
  return (
    <div className="shell">
      <nav className="side" aria-label="主导航">
        <div className="brand"><BrandMark />AI Learning Engine</div>
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}${n.optional ? ' optional' : ''}`}>
            {n.icon}<span>{n.label}</span>{n.soon && <span className="soon">开发中</span>}
          </NavLink>
        ))}
        <div className="side-foot"><ThemeSwitch /></div>
      </nav>
      <main className="main">
        <Suspense fallback={<Skeleton h={320} />}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/library" element={<Library />} />
            <Route path="/library/:id" element={<BookDetail />} />
            <Route path="/concept/:id" element={<ConceptPage />} />
            <Route path="/review" element={<Review />} />
            <Route path="/graph" element={<ComingSoon title="知识图谱" phase="Phase 4"
              intro="把整个知识库里的知识点和它们之间有原文依据的关系画成一张可以探索的网络。"
              items={['每本书的主题，以及不同书之间的共同知识点', '7 种关系：相似、支持、冲突、延伸、前置、应用、案例', '每条关系都附原文依据和可信度，没有依据的不画', '点击节点直接进入知识点详情和原始页码']} />} />
            <Route path="/cross-book" element={<ComingSoon title="跨书学习" phase="Phase 4"
              intro="直接提问，让 AI 比较你所有书里关于同一主题的观点。"
              items={['回答标注书名、作者、章节、页码和对应知识点', '作者原观点与 AI 归纳分开写', '证据不足时明确说明，不编造引用', '需要至少两本书，届时请上传第二本']} />} />
            <Route path="/insights" element={<ComingSoon title="学习洞察" phase="Phase 6"
              intro="用真实答题记录展示你的学习质量，而不是阅读页数和时长。"
              items={['理解、记忆、解释、推导、应用五个维度的掌握分布', '一个月、三个月后的长期记忆保持率', '薄弱点分析和下一步最值得做的学习任务', '跨书知识关联的数量与重要主题']} />} />
            <Route path="*" element={<div className="empty">没有这个页面。<NavLink to="/" style={{ color: 'var(--iris)' }}>回到总览</NavLink></div>} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}
