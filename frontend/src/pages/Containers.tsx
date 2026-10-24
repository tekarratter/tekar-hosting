import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { api, type Container } from '../api'
import { ErrorBox, Loading, PageHeader } from '../components/ui'
import ContainerTable from '../components/ContainerTable'
import { useIsStaff } from '../auth'

export default function Containers() {
  const staff = useIsStaff()
  const [q, setQ] = useState('')
  const [state, setState] = useState('')
  const r = useQuery({ queryKey: ['containers'], queryFn: () => api.get<Container[]>('/api/containers'), refetchInterval: 10000 })
  const list = (r.data || []).filter((c) => (!state || c.state === state) && (!q || `${c.name} ${c.image} ${c.project}`.toLowerCase().includes(q.toLowerCase())))
    .sort((a, b) => (a.project || 'zzz').localeCompare(b.project || 'zzz') || a.name.localeCompare(b.name))
  return (
    <div>
      <PageHeader title="Container" subtitle={r.data ? `${r.data.filter((c) => c.state === 'running').length}/${r.data.length} đang chạy` : undefined} />
      <div className="card">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
          <Search className="h-4 w-4 text-slate-400" />
          <input className="flex-1 bg-transparent text-sm outline-none" placeholder="Tìm theo tên, image, project…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="input w-auto py-1" value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">Mọi trạng thái</option><option value="running">running</option><option value="exited">exited</option><option value="restarting">restarting</option><option value="created">created</option>
          </select>
        </div>
        {r.isLoading && <Loading />}
        <ErrorBox error={r.error} />
        {r.data && <ContainerTable containers={list} canAct={staff} showProject onChanged={() => r.refetch()} />}
      </div>
      {!staff && <div className="mt-2 text-xs text-slate-500">Thao tác container nằm trong trang chi tiết ứng dụng.</div>}
    </div>
  )
}
