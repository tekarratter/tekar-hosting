import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Boxes, Container as CIcon, Globe, AlertTriangle, ShieldCheck, Archive, Cpu, MemoryStick, HardDrive } from 'lucide-react'
import { api } from '../api'
import { Badge, ErrorBox, Loading, PageHeader, ProgressBar, Section, SevSummary, Stat } from '../components/ui'
import { TimeArea } from '../components/charts'
import { fmtAgo, fmtBytes, fmtMB, pct } from '../util'
import { useIsStaff } from '../auth'

function Gauge({ label, icon, value, max, text, sub }: { label: string; icon: React.ReactNode; value: number; max: number; text: string; sub?: string }) {
  const p = pct(value, max)
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between text-xs font-medium text-slate-500">{label}{icon}</div>
      <div className="mt-1 flex items-baseline gap-2"><span className="text-2xl font-semibold">{p.toFixed(0)}%</span><span className="text-xs text-slate-500">{text}</span></div>
      <ProgressBar value={value} max={max} className="mt-2" />
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export default function Dashboard() {
  const staff = useIsStaff()
  const [hours, setHours] = useState(6)
  const d = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get('/api/dashboard'), refetchInterval: 15000 })
  const host = useQuery({ queryKey: ['metrics-host', hours], queryFn: () => api.get(`/api/metrics/host?hours=${hours}`), refetchInterval: 30000, enabled: staff })
  const top = useQuery({ queryKey: ['metrics-containers'], queryFn: () => api.get<any[]>('/api/metrics/containers'), refetchInterval: 15000 })
  const alloc = useQuery({ queryKey: ['allocation'], queryFn: () => api.get('/api/metrics/allocation'), refetchInterval: 30000 })
  const info = useQuery({ queryKey: ['sysinfo'], queryFn: () => api.get('/api/system/info'), staleTime: 300000 })

  if (d.isLoading) return <Loading />
  if (d.error) return <ErrorBox error={d.error} />
  const x = d.data
  const h = host.data?.latest
  const series = (host.data?.series || []).map((s: any) => ({ ...s, memPct: s.mem_total ? +(s.mem / s.mem_total * 100).toFixed(1) : 0 }))
  const a = alloc.data
  const hostCpus = a?.host?.cpus || info.data?.cpus || 0
  const hostMemMb = a?.host?.mem_total ? a.host.mem_total / 1048576 : 0
  const overCpu = hostCpus && a?.totals?.cpu_quota > hostCpus
  const overMem = hostMemMb && a?.totals?.mem_quota_mb > hostMemMb

  return (
    <div className="space-y-5">
      <PageHeader title="Dashboard" subtitle={info.data ? `${info.data.os} · Docker ${info.data.docker_version} · ${info.data.cpus} CPU · ${fmtBytes(info.data.mem_total)} RAM` : undefined} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Link to="/apps"><Stat label="Ứng dụng" value={x.apps} icon={<Boxes className="h-4 w-4" />} /></Link>
        <Link to="/containers"><Stat label="Container chạy" value={`${x.containers.running}/${x.containers.total}`} sub={x.containers.unhealthy ? <span className="text-rose-600">{x.containers.unhealthy} lỗi</span> : `${x.containers.stopped} dừng`} icon={<CIcon className="h-4 w-4" />} /></Link>
        <Link to="/proxy"><Stat label="Domain" value={x.routes.total} sub={x.routes.down ? <span className="text-rose-600">{x.routes.down} không phản hồi</span> : 'tất cả ổn'} icon={<Globe className="h-4 w-4" />} /></Link>
        <Link to="/alerts"><Stat label="Cảnh báo mở" value={x.alerts.length} icon={<AlertTriangle className="h-4 w-4" />} /></Link>
        <Link to="/backups"><Stat label="Backup lỗi" value={x.failed_backups} icon={<Archive className="h-4 w-4" />} /></Link>
        <Link to="/security"><Stat label="Bảo mật (config)" value={x.security ? (x.security.HIGH || 0) + (x.security.CRITICAL || 0) : '—'} sub={x.security ? <SevSummary summary={x.security} /> : 'chưa quét'} icon={<ShieldCheck className="h-4 w-4" />} /></Link>
      </div>

      {staff && h && (
        <div className="grid gap-3 md:grid-cols-3">
          <Gauge label="CPU" icon={<Cpu className="h-4 w-4" />} value={h.cpu_pct} max={100} text={`load ${h.load1?.toFixed(2)}`} />
          <Gauge label="RAM" icon={<MemoryStick className="h-4 w-4" />} value={h.mem_used} max={h.mem_total} text={`${fmtBytes(h.mem_used)} / ${fmtBytes(h.mem_total)}`} />
          <Gauge label="Ổ đĩa" icon={<HardDrive className="h-4 w-4" />} value={h.disk_used} max={h.disk_total} text={`${fmtBytes(h.disk_used)} / ${fmtBytes(h.disk_total)}`} />
        </div>
      )}

      {staff && (
        <Section title="Tài nguyên host" actions={
          <select className="input w-auto py-1 text-xs" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {[1, 6, 24, 72].map((v) => <option key={v} value={v}>{v} giờ</option>)}
          </select>
        }>
          <div className="grid gap-4 p-4 md:grid-cols-2">
            <div><div className="mb-1 text-xs font-medium text-slate-500">CPU %</div><TimeArea data={series} dataKey="cpu" unit="%" domain={[0, 100]} /></div>
            <div><div className="mb-1 text-xs font-medium text-slate-500">RAM %</div><TimeArea data={series} dataKey="memPct" unit="%" color="#10b981" domain={[0, 100]} /></div>
          </div>
        </Section>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        {x.alerts.length > 0 && (
          <Section title="Cảnh báo đang mở" actions={<Link to="/alerts" className="text-xs text-indigo-600">Xem tất cả</Link>}>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {x.alerts.map((al: any) => (
                <li key={al.id} className="flex items-start gap-2 px-4 py-2 text-sm">
                  <Badge color={al.level === 'critical' ? 'red' : al.level === 'warning' ? 'yellow' : 'blue'}>{al.level}</Badge>
                  <div className="flex-1"><div className="font-medium">{al.title}</div>{al.message && <div className="text-xs text-slate-500">{al.message}</div>}</div>
                  <span className="text-xs text-slate-400">{fmtAgo(al.created_at)}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}
        <Section title="Top container (RAM)">
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Container</th><th>CPU</th><th>RAM</th><th>Mạng rx/tx</th></tr></thead>
              <tbody>
                {(top.data || []).slice(0, 12).map((c: any) => (
                  <tr key={c.name}>
                    <td><div className="font-medium">{c.name}</div><div className="text-xs text-slate-500">{c.project || '—'}</div></td>
                    <td className="whitespace-nowrap">{c.cpu_pct.toFixed(1)}%</td>
                    <td className="min-w-[140px]">
                      <div className="text-xs">{fmtBytes(c.mem_bytes)}{c.mem_limit ? ` / ${fmtBytes(c.mem_limit)}` : ''}</div>
                      {c.mem_limit ? <ProgressBar value={c.mem_bytes} max={c.mem_limit} className="mt-1" /> : null}
                    </td>
                    <td className="whitespace-nowrap text-xs text-slate-500">{fmtBytes(c.net_rx)} / {fmtBytes(c.net_tx)}</td>
                  </tr>
                ))}
                {!top.data?.length && <tr><td colSpan={4} className="text-center text-slate-500">Chưa có số liệu (worker thu thập mỗi 30s)</td></tr>}
              </tbody>
            </table>
          </div>
        </Section>
      </div>

      {a && (
        <Section title="Cấp phát tài nguyên theo ứng dụng" actions={
          <div className="flex gap-2 text-xs text-slate-500">
            <span>Host: {hostCpus} CPU · {fmtMB(hostMemMb)} RAM</span>
            {overCpu ? <Badge color="orange">CPU over-commit {a.totals.cpu_quota}/{hostCpus}</Badge> : null}
            {overMem ? <Badge color="orange">RAM over-commit {fmtMB(a.totals.mem_quota_mb)}</Badge> : null}
          </div>
        }>
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Ứng dụng</th><th>CPU dùng / cấp / hạn mức</th><th>RAM dùng / cấp / hạn mức</th><th>Disk dùng / hạn mức</th></tr></thead>
              <tbody>
                {a.apps.map((r: any) => {
                  const cpuMax = r.cpu_allocated || r.cpu_quota
                  const memMax = r.mem_allocated_mb || r.mem_quota_mb
                  return (
                    <tr key={r.app_id}>
                      <td><Link to={`/apps/${r.app_id}`} className="font-medium text-indigo-600 dark:text-indigo-400">{r.name}</Link></td>
                      <td className="min-w-[180px]">
                        <div className="text-xs">{r.cpu_used.toFixed(2)} / {r.cpu_allocated ?? '—'} / {r.cpu_quota ?? '∞'} core</div>
                        {cpuMax ? <ProgressBar value={r.cpu_used} max={cpuMax} className="mt-1" /> : null}
                      </td>
                      <td className="min-w-[180px]">
                        <div className="text-xs">{fmtMB(r.mem_used_mb)} / {fmtMB(r.mem_allocated_mb)} / {r.mem_quota_mb ? fmtMB(r.mem_quota_mb) : '∞'}</div>
                        {memMax ? <ProgressBar value={r.mem_used_mb} max={memMax} className="mt-1" /> : null}
                      </td>
                      <td className="min-w-[150px]">
                        <div className="text-xs">{fmtMB(r.disk_used_mb)} / {r.disk_quota_mb ? fmtMB(r.disk_quota_mb) : '∞'}</div>
                        {r.disk_quota_mb ? <ProgressBar value={r.disk_used_mb || 0} max={r.disk_quota_mb} className="mt-1" /> : null}
                      </td>
                    </tr>
                  )
                })}
                {a.unmanaged?.length > 0 && a.unmanaged.map((u: any) => (
                  <tr key={u.project} className="text-slate-500">
                    <td><span className="italic">{u.project}</span> <Badge>chưa quản lý</Badge></td>
                    <td className="text-xs">{u.cpu_used.toFixed(2)} core</td>
                    <td className="text-xs">{fmtMB(u.mem_used_mb)}</td>
                    <td>—</td>
                  </tr>
                ))}
                {!a.apps.length && !a.unmanaged?.length && <tr><td colSpan={4} className="text-center text-slate-500">Chưa có ứng dụng</td></tr>}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  )
}
