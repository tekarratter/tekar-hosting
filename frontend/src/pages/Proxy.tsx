import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, RefreshCw, FileJson } from 'lucide-react'
import { api, type Route, type RouteKind } from '../api'
import { Badge, ErrorBox, Loading, PageHeader, Section, Spinner, Tabs } from '../components/ui'
import RouteForm from '../components/RouteForm'
import { RouteTable } from '../components/RouteTable'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { useIsStaff } from '../auth'
import AccessLists from './proxy/AccessLists'
import Certificates from './proxy/Certificates'
import Streams from './proxy/Streams'
import AccessLog from './proxy/AccessLog'
import ProxySettings from './proxy/ProxySettings'
import NpmImport from './proxy/NpmImport'

export default function Proxy() {
  const toast = useToast()
  const staff = useIsStaff()
  const [tab, setTab] = useState('hosts')
  const [kind, setKind] = useState<RouteKind | ''>('')
  const [search, setSearch] = useState('')
  const routes = useQuery({ queryKey: ['routes'], queryFn: () => api.get<Route[]>('/api/proxy/routes'), refetchInterval: 30000 })
  const status = useQuery({ queryKey: ['proxy-status'], queryFn: () => api.get('/api/proxy/status'), refetchInterval: 60000 })
  const preview = useQuery({ queryKey: ['proxy-preview'], queryFn: () => api.get('/api/proxy/preview'), enabled: tab === 'config' && staff })
  const [edit, setEdit] = useState<Route | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<{ id: number; title: string } | null>(null)
  const onJob = (id: number, title: string) => setJob({ id, title })

  const all = routes.data || []
  const count = (k: RouteKind) => all.filter((r) => (r.kind || 'proxy') === k).length
  const list = all.filter((r) => (!kind || (r.kind || 'proxy') === kind) && (!search || `${r.hosts.join(' ')} ${r.upstreams.join(' ')} ${r.app_name || ''}`.toLowerCase().includes(search.toLowerCase())))
  const st = status.data

  return (
    <div>
      <PageHeader title="Proxy & Domain" subtitle={st && (
        <span className="flex flex-wrap items-center gap-2">
          Caddy {st.online ? <Badge color="green">online</Badge> : <Badge color="red">offline</Badge>}
          {st.ports && <span className="font-mono text-xs">HTTP :{st.ports.http} · HTTPS :{st.ports.https}</span>}
          {st.public ? <Badge color="green">giữ cổng public</Badge> : <Badge color="yellow">song song NPM</Badge>}
          {(st.npm || []).map((n: any) => <Badge key={n.name} color={n.state === 'running' ? 'orange' : 'gray'}>NPM {n.name}: {n.state}</Badge>)}
        </span>
      )} actions={<>
        {staff && <button className="btn-secondary" disabled={busy} onClick={async () => {
          setBusy(true)
          try { const r = await api.post('/api/proxy/apply'); toast.success(`Caddy: ${r.caddy}${r.tunnel ? ` · Tunnel: ${r.tunnel}` : ''}`) } catch (e) { toast.error(e) } finally { setBusy(false) }
        }}>{busy ? <Spinner /> : <RefreshCw className="h-4 w-4" />}Áp dụng lại</button>}
        <button className="btn-primary" onClick={() => setEdit(null)}><Plus className="h-4 w-4" />Thêm host</button>
      </>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'hosts', label: `Hosts (${all.length})` },
        { id: 'acl', label: 'Access lists' },
        { id: 'certs', label: 'Chứng chỉ' },
        { id: 'streams', label: 'Streams', hidden: !staff },
        { id: 'logs', label: 'Access log' },
        { id: 'settings', label: 'Cài đặt & chuyển đổi', hidden: !staff },
        { id: 'npm', label: 'Import NPM', hidden: !staff },
        { id: 'config', label: 'Cấu hình Caddy', hidden: !staff },
      ]} />
      {tab === 'hosts' && (
        <div className="card">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
            {([['', `Tất cả (${all.length})`], ['proxy', `Proxy host (${count('proxy')})`], ['redirect', `Redirection (${count('redirect')})`], ['dead', `404 host (${count('dead')})`]] as [RouteKind | '', string][]).map(([k, l]) => (
              <button key={k} className={kind === k ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setKind(k)}>{l}</button>
            ))}
            <input className="input ml-auto w-64" placeholder="Tìm domain, upstream…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <ErrorBox error={routes.error} />
          <RouteTable routes={list} loading={routes.isLoading} canEdit onEdit={setEdit} onChanged={() => routes.refetch()} />
        </div>
      )}
      {tab === 'acl' && <AccessLists canEdit={staff} />}
      {tab === 'certs' && <Certificates canEdit={staff} />}
      {tab === 'streams' && staff && <Streams onJob={onJob} />}
      {tab === 'logs' && <AccessLog routes={all} />}
      {tab === 'settings' && staff && <ProxySettings onJob={onJob} />}
      {tab === 'npm' && staff && <NpmImport onDone={() => routes.refetch()} onJob={onJob} />}
      {tab === 'config' && staff && (
        <Section title={<span className="flex items-center gap-1"><FileJson className="h-4 w-4" />JSON đẩy vào Caddy (secret đã che)</span>}>
          {preview.isLoading ? <Loading /> : <pre className="max-h-[70vh] overflow-auto bg-slate-950 p-3 font-mono text-xs text-slate-200">{JSON.stringify(preview.data, null, 2)}</pre>}
        </Section>
      )}
      {edit !== undefined && <RouteForm open route={edit} onClose={() => setEdit(undefined)} onSaved={() => routes.refetch()} />}
      <JobModal jobId={job?.id ?? null} title={job?.title} onClose={() => { setJob(null); routes.refetch(); status.refetch() }} />
    </div>
  )
}
