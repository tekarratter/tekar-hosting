import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCheck } from 'lucide-react'
import { api } from '../api'
import { Badge, ErrorBox, Loading, PageHeader, Tabs } from '../components/ui'
import { fmtDate } from '../util'

export default function Alerts() {
  const [tab, setTab] = useState('open')
  const q = useQuery({ queryKey: ['alerts', tab], queryFn: () => api.get<any[]>(`/api/alerts?resolved=${tab === 'resolved'}`), refetchInterval: 15000 })
  return (
    <div>
      <PageHeader title="Cảnh báo" subtitle="Tự động phát hiện: tài nguyên cao, container lỗi, domain down, backup/job thất bại, lỗ hổng" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'open', label: 'Đang mở' }, { id: 'resolved', label: 'Đã xử lý' }]} />
      <ErrorBox error={q.error} />
      <div className="card overflow-x-auto">
        {q.isLoading ? <Loading /> : (
          <table className="table">
            <thead><tr><th>Mức</th><th>Nội dung</th><th>Thời gian</th><th></th></tr></thead>
            <tbody>
              {(q.data || []).map((a) => (
                <tr key={a.id}>
                  <td><Badge color={a.level === 'critical' ? 'red' : a.level === 'warning' ? 'yellow' : 'blue'}>{a.level}</Badge></td>
                  <td><div className="font-medium">{a.title}</div>{a.message && <div className="text-xs text-slate-500">{a.message}</div>}<div className="font-mono text-[11px] text-slate-400">{a.key}</div></td>
                  <td className="whitespace-nowrap text-xs">{fmtDate(a.created_at)}{a.resolved_at && <div className="text-emerald-600">✓ {fmtDate(a.resolved_at)}</div>}</td>
                  <td className="text-right">{!a.resolved && <button className="btn-ghost btn-sm" onClick={async () => { await api.post(`/api/alerts/${a.id}/resolve`); q.refetch() }}><CheckCheck className="h-3.5 w-3.5" />Đóng</button>}</td>
                </tr>
              ))}
              {!q.data?.length && <tr><td colSpan={4} className="text-center text-slate-500">Không có cảnh báo</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
