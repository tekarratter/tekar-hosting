import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api, type Route } from '../../api'
import { ErrorBox, Loading, Section, Toggle } from '../../components/ui'
import { cls } from '../../util'

interface LogRow { ts: number; host: string; method: string; uri: string; status: number; duration_ms: number; size: number; ip: string; ua: string; proto: string; tls: boolean }

const statusColor = (s: number) => s >= 500 ? 'text-rose-600' : s >= 400 ? 'text-amber-600' : s >= 300 ? 'text-sky-600' : 'text-emerald-600'

export default function AccessLog({ routes }: { routes: Route[] }) {
  const [host, setHost] = useState('')
  const [statusMin, setStatusMin] = useState('')
  const [limit, setLimit] = useState(200)
  const [auto, setAuto] = useState(false)
  const hosts = [...new Set(routes.flatMap((r) => r.hosts))].sort()
  const qs = new URLSearchParams({ limit: String(limit) })
  if (host) qs.set('host', host)
  if (statusMin) qs.set('status_min', statusMin)
  const q = useQuery({ queryKey: ['access-log', host, statusMin, limit], queryFn: () => api.get<LogRow[]>(`/api/proxy/logs?${qs}`), refetchInterval: auto ? 5000 : false })
  return (
    <Section title="Access log" actions={<>
      <select className="input w-56" value={host} onChange={(e) => setHost(e.target.value)}>
        <option value="">Mọi domain</option>
        {hosts.map((h) => <option key={h}>{h}</option>)}
      </select>
      <select className="input w-32" value={statusMin} onChange={(e) => setStatusMin(e.target.value)}>
        <option value="">Mọi status</option><option value="400">≥ 400</option><option value="500">≥ 500</option>
      </select>
      <select className="input w-24" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
        {[100, 200, 500, 1000].map((n) => <option key={n}>{n}</option>)}
      </select>
      <Toggle checked={auto} onChange={setAuto} label="Tự làm mới 5s" />
      <button className="btn-ghost btn-sm" onClick={() => q.refetch()}><RefreshCw className="h-3.5 w-3.5" /></button>
    </>}>
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      <div className="overflow-x-auto">
        <table className="table">
          <thead><tr><th>Thời gian</th><th>Host</th><th>Request</th><th>Status</th><th>Thời gian xử lý</th><th>Kích thước</th><th>IP</th><th>User-Agent</th></tr></thead>
          <tbody>
            {(q.data || []).map((l, i) => (
              <tr key={i}>
                <td className="whitespace-nowrap text-xs">{new Date(l.ts * 1000).toLocaleString('vi-VN', { hour12: false })}</td>
                <td className="text-xs">{l.host}</td>
                <td className="mono max-w-md break-all"><b>{l.method}</b> {l.uri}</td>
                <td className={cls('font-mono font-semibold', statusColor(l.status))}>{l.status}</td>
                <td className="whitespace-nowrap text-xs">{l.duration_ms} ms</td>
                <td className="whitespace-nowrap text-xs">{l.size ?? '—'}</td>
                <td className="mono">{l.ip}</td>
                <td className="max-w-xs truncate text-xs text-slate-500" title={l.ua}>{l.ua}</td>
              </tr>
            ))}
            {q.data && !q.data.length && <tr><td colSpan={8} className="text-center text-slate-500">Chưa có request</td></tr>}
          </tbody>
        </table>
      </div>
    </Section>
  )
}
