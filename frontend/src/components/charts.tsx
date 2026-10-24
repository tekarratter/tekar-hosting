import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fmtTime } from '../util'

const COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#0ea5e9', '#a855f7', '#14b8a6', '#f97316', '#84cc16', '#ec4899']

export function TimeArea({ data, dataKey, color = '#6366f1', unit = '', height = 160, domain, format }: {
  data: any[]; dataKey: string; color?: string; unit?: string; height?: number; domain?: [number, number | string]; format?: (v: number) => string
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-200 dark:text-slate-800" />
        <XAxis dataKey="t" tickFormatter={fmtTime} fontSize={11} stroke="#94a3b8" minTickGap={40} />
        <YAxis fontSize={11} stroke="#94a3b8" width={45} domain={domain} tickFormatter={(v) => (format ? format(v) : `${v}${unit}`)} />
        <Tooltip labelFormatter={(l) => new Date(l).toLocaleString('vi-VN')} formatter={(v: number) => (format ? format(v) : `${v}${unit}`)} contentStyle={{ fontSize: 12 }} />
        <Area type="monotone" dataKey={dataKey} stroke={color} fill={`url(#g-${dataKey})`} strokeWidth={1.5} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function MultiLine({ series, height = 220, format }: { series: Record<string, { t: string; v: number }[]>; height?: number; format?: (v: number) => string }) {
  const names = Object.keys(series)
  const map = new Map<string, any>()
  names.forEach((n) => series[n].forEach((p) => {
    const row = map.get(p.t) || { t: p.t }
    row[n] = p.v
    map.set(p.t, row)
  }))
  const data = [...map.values()].sort((a, b) => new Date(a.t).getTime() - new Date(b.t).getTime())
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-200 dark:text-slate-800" />
        <XAxis dataKey="t" tickFormatter={fmtTime} fontSize={11} stroke="#94a3b8" minTickGap={40} />
        <YAxis fontSize={11} stroke="#94a3b8" width={55} tickFormatter={(v) => (format ? format(v) : String(v))} />
        <Tooltip labelFormatter={(l) => new Date(l).toLocaleString('vi-VN')} formatter={(v: number) => (format ? format(v) : v)} contentStyle={{ fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {names.map((n, i) => <Line key={n} type="monotone" dataKey={n} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.5} isAnimationActive={false} />)}
      </LineChart>
    </ResponsiveContainer>
  )
}
