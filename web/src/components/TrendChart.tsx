import { useEffect, useRef } from 'react'
import { cssVar, useIsDark } from '../hooks'

const loadEcharts = () => import('./echartsLine').then(m => m.default)

export interface TrendPoint { day: string; mastered: number; evidence: number; attempts: number; avg_score: number | null }

/** Mastered vs. tested concepts over time. ECharts is loaded only when this chart is on screen. */
export function TrendChart({ points }: { points: TrendPoint[] }) {
  const el = useRef<HTMLDivElement>(null)
  const dark = useIsDark()
  useEffect(() => {
    let chart: any, ro: ResizeObserver | undefined, disposed = false
    ;(async () => {
      const echarts = await loadEcharts()
      if (disposed || !el.current) return
      chart = echarts.init(el.current, undefined, { renderer: 'svg' })
      const ink3 = cssVar('--ink-3'), line = cssVar('--line'), font = cssVar('--font')
      chart.setOption({
        animationDuration: 500, textStyle: { fontFamily: font },
        grid: { left: 8, right: 12, top: 28, bottom: 4, containLabel: true },
        legend: { top: 0, left: 0, icon: 'circle', itemWidth: 8, textStyle: { color: ink3, fontSize: 12 } },
        tooltip: { trigger: 'axis', backgroundColor: cssVar('--bg-raised'), borderColor: line, textStyle: { color: cssVar('--ink'), fontSize: 12 } },
        xAxis: { type: 'category', data: points.map(p => p.day.slice(5)), axisLine: { lineStyle: { color: line } }, axisTick: { show: false }, axisLabel: { color: ink3 } },
        yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: line } }, axisLabel: { color: ink3 } },
        series: [
          { name: '已测试的知识点', type: 'line', data: points.map(p => p.evidence), smooth: 0.3, symbol: 'none', lineStyle: { width: 1.5, color: cssVar('--m-apply') }, itemStyle: { color: cssVar('--m-apply') } },
          { name: '已掌握', type: 'line', data: points.map(p => p.mastered), smooth: 0.3, symbol: 'circle', symbolSize: 5, lineStyle: { width: 2.5, color: cssVar('--m-mastered') }, itemStyle: { color: cssVar('--m-mastered') },
            areaStyle: { color: cssVar('--m-mastered'), opacity: 0.08 } },
        ],
      })
      ro = new ResizeObserver(() => chart.resize())
      ro.observe(el.current)
    })()
    return () => { disposed = true; ro?.disconnect(); chart?.dispose() }
  }, [points, dark])
  return <div ref={el} style={{ height: 220 }} role="img" aria-label="掌握知识点数量随时间的变化" />
}
