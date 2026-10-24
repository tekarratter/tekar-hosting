import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { Badge, Section, StatusBadge } from '../../components/ui'
import ContainerTable from '../../components/ContainerTable'
import DevboxCard from '../../components/DevboxCard'
import { fmtAgo, fmtBytes, fmtDate } from '../../util'
import type { TabProps } from '../AppDetail'

export default function OverviewTab({ app, refetch, canDeploy, runJob }: TabProps) {
  const ov = useQuery({ queryKey: ['app-overview', app.id], queryFn: () => api.get(`/api/apps/${app.id}/overview`) })
  const jobs = useQuery({ queryKey: ['app-jobs', app.id], queryFn: () => api.get<any[]>(`/api/apps/${app.id}/jobs?limit=10`), refetchInterval: 10000 })
  return (
    <div className="space-y-4">
      <div className="card grid gap-1 p-4 text-xs md:grid-cols-2">
        <div>Thư mục compose: <span className="font-mono">{app.work_dir}</span></div>
        <div>Project: <span className="font-mono">{app.compose_project}</span> <Badge color={app.kind === 'managed' ? 'blue' : 'purple'}>{app.kind}</Badge>{app.converted_from && <> <Badge color="orange">chuyển từ {app.converted_from}</Badge></>}</div>
        {app.volumes_dir && <div>Dữ liệu volume: <span className="font-mono">{app.volumes_dir}</span></div>}
        {app.data_dir && <div>Data mount (APP_DATA_DIR): <span className="font-mono">{app.data_dir}</span></div>}
      </div>
      {app.template === 'devbox' && <DevboxCard app={app} canDeploy={canDeploy} runJob={runJob} />}
      <Section title="Container">
        <ContainerTable containers={app.containers || []} canAct={canDeploy} onChanged={refetch} />
      </Section>
      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="Domain" actions={<Link to={`/apps/${app.id}/domains`} className="text-xs text-indigo-600">Quản lý</Link>}>
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {(ov.data?.routes || []).map((r: any) => (
              <li key={r.id} className="flex items-center justify-between px-4 py-2">
                <span>{r.hosts.map((h: string) => <a key={h} href={`https://${h}`} target="_blank" rel="noreferrer" className="mr-2 text-indigo-600 hover:underline">{h}</a>)}</span>
                <span>{!r.enabled ? <Badge>tắt</Badge> : r.health ? <StatusBadge status={r.health} /> : null}</span>
              </li>
            ))}
            {!ov.data?.routes?.length && <li className="px-4 py-3 text-slate-500">Chưa có domain</li>}
          </ul>
        </Section>
        <Section title="Dịch vụ nền tảng" actions={<Link to={`/apps/${app.id}/services`} className="text-xs text-indigo-600">Quản lý</Link>}>
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {(ov.data?.bindings || []).map((b: any) => (
              <li key={b.id} className="flex items-center gap-2 px-4 py-2"><Badge color="purple">{b.kind}</Badge>{b.service}<span className="ml-auto font-mono text-xs">{b.resource}</span></li>
            ))}
            {!ov.data?.bindings?.length && <li className="px-4 py-3 text-slate-500">Không dùng</li>}
          </ul>
        </Section>
        <Section title="Backup" actions={<Link to={`/apps/${app.id}/backup`} className="text-xs text-indigo-600">Quản lý</Link>}>
          <div className="space-y-1 px-4 py-3 text-sm">
            <div>Lịch: {app.backup_cron ? <span className="font-mono">{app.backup_cron}</span> : <Badge color="yellow">chưa đặt lịch</Badge>}</div>
            {ov.data?.last_backup ? (
              <div>Gần nhất: <StatusBadge status={ov.data.last_backup.status} /> {fmtAgo(ov.data.last_backup.started_at)} · {fmtBytes(ov.data.last_backup.size)}</div>
            ) : <div className="text-slate-500">Chưa có bản backup</div>}
          </div>
        </Section>
      </div>
      <Section title="Hoạt động gần đây">
        <table className="table">
          <tbody>
            {(jobs.data || []).map((j) => (
              <tr key={j.id}><td className="w-16 font-mono text-xs"><Link to={`/jobs/${j.id}`} className="text-indigo-600">#{j.id}</Link></td><td>{j.kind}</td><td><StatusBadge status={j.status} /></td><td className="text-xs text-slate-500">{fmtDate(j.created_at)}</td></tr>
            ))}
            {!jobs.data?.length && <tr><td className="text-slate-500">Chưa có</td></tr>}
          </tbody>
        </table>
      </Section>
    </div>
  )
}
