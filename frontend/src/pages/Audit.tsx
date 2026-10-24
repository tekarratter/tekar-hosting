import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Badge, ErrorBox, Loading, PageHeader } from '../components/ui'
import { fmtDate } from '../util'

export default function Audit() {
  const [q, setQ] = useState('')
  const [action, setAction] = useState('')
  const r = useQuery({ queryKey: ['audit', q, action], queryFn: () => api.get<any[]>(`/api/audit?limit=300${q ? `&q=${encodeURIComponent(q)}` : ''}${action ? `&action=${action}` : ''}`) })
  return (
    <div>
      <PageHeader title="Nhật ký thao tác" subtitle="Mọi thay đổi trên panel, terminal, Claude, webhook" />
      <div className="card">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
          <input className="input w-64" placeholder="Tìm theo user / đối tượng" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="input w-auto" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">Mọi hành động</option>
            {['auth', 'app', 'container', 'terminal', 'proxy', 'platform', 'backup', 'cloudflare', 'user', 'settings', 'token', 'claude', 'system'].map((a) => <option key={a} value={a}>{a}.*</option>)}
          </select>
        </div>
        <ErrorBox error={r.error} />
        {r.isLoading ? <Loading /> : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Thời gian</th><th>Người dùng</th><th>Hành động</th><th>Đối tượng</th><th>Chi tiết</th><th>IP</th></tr></thead>
              <tbody>
                {(r.data || []).map((a) => (
                  <tr key={a.id}>
                    <td className="whitespace-nowrap text-xs">{fmtDate(a.created_at)}</td>
                    <td className="text-xs">{a.user}</td>
                    <td><Badge color={a.action.includes('delete') || a.action.includes('failed') ? 'red' : a.action.startsWith('terminal') ? 'yellow' : 'gray'}>{a.action}</Badge></td>
                    <td className="mono">{a.target}</td>
                    <td className="mono max-w-sm truncate text-slate-500" title={JSON.stringify(a.detail)}>{Object.keys(a.detail || {}).length ? JSON.stringify(a.detail) : ''}</td>
                    <td className="mono text-slate-500">{a.ip}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
