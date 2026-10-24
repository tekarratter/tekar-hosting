import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Play, Square, RotateCw, Rocket, ArrowLeft, Copy, Github } from 'lucide-react'
import { api, type AppT } from '../api'
import { Badge, ErrorBox, Loading, Modal, StatusBadge, Tabs } from '../components/ui'
import CloneForm from '../components/CloneForm'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { useAuth, useIsStaff } from '../auth'
import OverviewTab from './app/OverviewTab'
import ComposeTab from './app/ComposeTab'
import LogsTab from './app/LogsTab'
import TerminalTab from './app/TerminalTab'
import ResourcesTab from './app/ResourcesTab'
import DomainsTab from './app/DomainsTab'
import ServicesTab from './app/ServicesTab'
import BackupTab from './app/BackupTab'
import SecurityTab from './app/SecurityTab'
import MembersTab from './app/MembersTab'
import WebhookTab from './app/WebhookTab'
import SettingsTab from './app/SettingsTab'
import FilesTab from './app/FilesTab'
import GitTab from './app/GitTab'

export interface TabProps { app: AppT; refetch: () => void; runJob: (p: Promise<{ job_id: number }>, title?: string) => void; canDeploy: boolean; isOwner: boolean }

export default function AppDetail() {
  const { id, tab = 'overview' } = useParams()
  const nav = useNavigate()
  const loc = useLocation()
  const toast = useToast()
  const staff = useIsStaff()
  const { user } = useAuth()
  const [clone, setClone] = useState(false)
  const [job, setJob] = useState<{ id: number; title: string } | null>(null)
  const q = useQuery({ queryKey: ['app', id], queryFn: () => api.get<AppT>(`/api/apps/${id}`), refetchInterval: 10000 })

  useEffect(() => {
    const j = (loc.state as any)?.job
    if (j) { setJob({ id: j, title: 'Deploy' }); nav(loc.pathname, { replace: true, state: null }) }
  }, [loc.state, loc.pathname, nav])

  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  const app = q.data!
  const canDeploy = app.my_role === 'owner' || app.my_role === 'deployer'
  const isOwner = app.my_role === 'owner'
  const runJob = (p: Promise<{ job_id: number }>, title = 'Tiến trình') => {
    p.then((r) => setJob({ id: r.job_id, title })).catch((e) => toast.error(e))
  }
  const props: TabProps = { app, refetch: () => q.refetch(), runJob, canDeploy, isOwner }
  const running = (app.containers || []).filter((c: any) => c.state === 'running').length
  const total = (app.containers || []).length
  const status = !total ? 'stopped' : running === total ? 'running' : running ? 'partial' : 'stopped'

  const tabs = [
    { id: 'overview', label: 'Tổng quan' },
    { id: 'compose', label: 'Compose & Env', hidden: !canDeploy },
    { id: 'git', label: 'Git', hidden: !canDeploy || app.kind !== 'managed' },
    { id: 'files', label: 'Thư mục', hidden: !canDeploy || (app.kind === 'adopted' && app.compose_accessible === false) },
    { id: 'logs', label: 'Logs' },
    { id: 'terminal', label: 'Terminal', hidden: !canDeploy },
    { id: 'resources', label: 'Tài nguyên' },
    { id: 'domains', label: 'Domain' },
    { id: 'services', label: 'Dịch vụ' },
    { id: 'backup', label: 'Backup' },
    { id: 'security', label: 'Bảo mật' },
    { id: 'members', label: 'Thành viên' },
    { id: 'webhook', label: 'Webhook', hidden: !isOwner },
    { id: 'settings', label: 'Cài đặt', hidden: !canDeploy },
  ]

  return (
    <div>
      <button className="btn-ghost btn-sm mb-2 -ml-2" onClick={() => nav('/apps')}><ArrowLeft className="h-4 w-4" />Ứng dụng</button>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">{app.name}</h1>
            <StatusBadge status={status} />
            <Badge color={app.kind === 'managed' ? 'blue' : 'purple'}>{app.kind}</Badge>
            {app.template && <Badge>{app.template}</Badge>}
            {app.converted_from && <Badge color="orange">chuyển từ {app.converted_from}</Badge>}
            {app.git?.repo && <Badge color="gray"><span className="flex items-center gap-1"><Github className="h-3 w-3" />{app.git.repo.replace(/^https?:\/\/[^/]+\//, '')}{app.git.commit ? `@${app.git.commit.slice(0, 7)}` : ''}</span></Badge>}
          </div>
          <div className="mt-0.5 text-sm text-slate-500"><span className="font-mono">{app.compose_project}</span> · {app.work_dir}{app.description ? ` · ${app.description}` : ''}</div>
        </div>
        {canDeploy && (
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={() => runJob(api.post(`/api/apps/${app.id}/deploy`), 'Deploy')}><Rocket className="h-4 w-4" />Deploy</button>
            <button className="btn-secondary" onClick={() => runJob(api.post(`/api/apps/${app.id}/action/start`), 'Start')}><Play className="h-4 w-4" />Start</button>
            <button className="btn-secondary" onClick={() => runJob(api.post(`/api/apps/${app.id}/action/restart`), 'Restart')}><RotateCw className="h-4 w-4" />Restart</button>
            <button className="btn-secondary" onClick={() => runJob(api.post(`/api/apps/${app.id}/action/stop`), 'Stop')}><Square className="h-4 w-4" />Stop</button>
            {user?.role !== 'member' && <button className="btn-secondary" onClick={() => setClone(true)}><Copy className="h-4 w-4" />Clone</button>}
          </div>
        )}
      </div>
      <Tabs tabs={tabs} value={tab} onChange={(t) => nav(`/apps/${app.id}/${t}`)} />
      {tab === 'overview' && <OverviewTab {...props} />}
      {tab === 'compose' && <ComposeTab {...props} />}
      {tab === 'files' && <FilesTab {...props} />}
      {tab === 'git' && <GitTab {...props} />}
      {tab === 'logs' && <LogsTab {...props} />}
      {tab === 'terminal' && <TerminalTab {...props} />}
      {tab === 'resources' && <ResourcesTab {...props} staff={staff} />}
      {tab === 'domains' && <DomainsTab {...props} />}
      {tab === 'services' && <ServicesTab {...props} />}
      {tab === 'backup' && <BackupTab {...props} />}
      {tab === 'security' && <SecurityTab {...props} />}
      {tab === 'members' && <MembersTab {...props} />}
      {tab === 'webhook' && <WebhookTab {...props} />}
      {tab === 'settings' && <SettingsTab {...props} />}
      <Modal open={clone} onClose={() => setClone(false)} title={`Clone ${app.name}`} size="lg">
        {clone && <CloneForm sourceId={app.id} onCancel={() => setClone(false)} onCreated={(nid, j) => { setClone(false); nav(`/apps/${nid}`, { state: { job: j } }) }} />}
      </Modal>
      <JobModal jobId={job?.id ?? null} title={job?.title} onClose={() => { setJob(null); q.refetch() }} />
    </div>
  )
}
