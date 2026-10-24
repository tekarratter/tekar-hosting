import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus, Search, Radar, Boxes, Github } from 'lucide-react'
import { api, type AppT } from '../api'
import { Badge, Empty, ErrorBox, Loading, PageHeader, StatusBadge } from '../components/ui'
import CreateWizard from '../components/CreateWizard'
import JobModal from '../components/JobModal'
import { useAuth, useIsStaff } from '../auth'
import { fmtAgo, fmtMB } from '../util'

export default function Apps() {
  const nav = useNavigate()
  const { user } = useAuth()
  const staff = useIsStaff()
  const [q, setQ] = useState('')
  const [create, setCreate] = useState(false)
  const [job, setJob] = useState<number | null>(null)
  const apps = useQuery({ queryKey: ['apps'], queryFn: () => api.get<AppT[]>('/api/apps'), refetchInterval: 15000 })
  const list = (apps.data || []).filter((a) => !q || `${a.name} ${a.slug} ${a.compose_project}`.toLowerCase().includes(q.toLowerCase()))

  return (
    <div>
      <PageHeader title="Ứng dụng" subtitle="Dự án docker compose do panel quản lý" actions={<>
        {staff && <Link to="/stacks" className="btn-secondary"><Radar className="h-4 w-4" />Stack trên host</Link>}
        {user?.role !== 'member' && <button className="btn-primary" onClick={() => setCreate(true)}><Plus className="h-4 w-4" />Tạo app</button>}
      </>} />
      <div className="card">
        <div className="flex items-center gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
          <Search className="h-4 w-4 text-slate-400" />
          <input className="flex-1 bg-transparent text-sm outline-none" placeholder="Tìm ứng dụng…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {apps.isLoading && <Loading />}
        <ErrorBox error={apps.error} />
        {apps.data && !list.length && <Empty><Boxes className="mx-auto mb-2 h-8 w-8 text-slate-300" />Chưa có ứng dụng. Tạo mới hoặc vào "Stack trên host" để quản lý / chuyển dự án có sẵn.</Empty>}
        {list.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Tên</th><th>Loại</th><th>Trạng thái</th><th>Container</th><th>Hạn mức</th><th>Disk</th><th>Deploy</th><th>Quyền</th></tr></thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.id} className="cursor-pointer" onClick={() => nav(`/apps/${a.id}`)}>
                    <td><Link to={`/apps/${a.id}`} className="font-medium text-indigo-600 dark:text-indigo-400">{a.name}</Link><div className="mono text-slate-500">{a.compose_project}</div></td>
                    <td><Badge color={a.kind === 'managed' ? 'blue' : 'purple'}>{a.kind}</Badge>{a.git?.repo && <span className="ml-1 inline-flex" title={a.git.repo}><Github className="h-3.5 w-3.5 text-slate-500" /></span>}{a.converted_from && <div className="mt-0.5"><Badge color="orange">từ {a.converted_from}</Badge></div>}</td>
                    <td><StatusBadge status={a.status} /></td>
                    <td>{a.running}/{a.containers as any}</td>
                    <td className="text-xs">{a.cpu_limit ? `${a.cpu_limit} CPU` : '—'} · {a.mem_limit_mb ? fmtMB(a.mem_limit_mb) : '—'}</td>
                    <td className="text-xs">{fmtMB(a.disk_usage_mb)}{a.disk_quota_mb ? ` / ${fmtMB(a.disk_quota_mb)}` : ''}</td>
                    <td className="text-xs text-slate-500">{fmtAgo(a.last_deployed_at)}</td>
                    <td><Badge>{a.my_role}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <CreateWizard open={create} onClose={() => setCreate(false)} onCreated={(id, j) => { setCreate(false); apps.refetch(); nav(`/apps/${id}`, { state: { job: j } }) }} />
      <JobModal jobId={job} onClose={() => setJob(null)} title="Deploy" />
    </div>
  )
}
