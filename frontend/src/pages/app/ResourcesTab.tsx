import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { Field, Section, Spinner } from '../../components/ui'
import { MultiLine } from '../../components/charts'
import { useToast } from '../../components/toast'
import { fmtBytes, fmtMB } from '../../util'
import type { TabProps } from '../AppDetail'

export default function ResourcesTab({ app, runJob, canDeploy, staff }: TabProps & { staff: boolean }) {
  const toast = useToast()
  const [hours, setHours] = useState(6)
  const m = useQuery({ queryKey: ['app-metrics', app.id, hours], queryFn: () => api.get(`/api/metrics/apps/${app.id}?hours=${hours}`), refetchInterval: 30000 })
  const services = app.available_services || []
  const [limits, setLimits] = useState<Record<string, { cpus?: string; memory_mb?: string }>>(() =>
    Object.fromEntries(services.map((s) => [s, { cpus: app.service_limits?.[s]?.cpus?.toString() || '', memory_mb: app.service_limits?.[s]?.memory_mb?.toString() || '' }])))
  const [cpuQ, setCpuQ] = useState(app.cpu_limit?.toString() || '')
  const [memQ, setMemQ] = useState(app.mem_limit_mb?.toString() || '')
  const [diskQ, setDiskQ] = useState(app.disk_quota_mb?.toString() || '')
  const [edge, setEdge] = useState<string[]>(app.edge_services || [])
  const [busy, setBusy] = useState(false)

  const series = m.data?.containers || {}
  const cpu = Object.fromEntries(Object.entries(series).map(([n, pts]: any) => [n, pts.map((p: any) => ({ t: p.t, v: p.cpu }))]))
  const mem = Object.fromEntries(Object.entries(series).map(([n, pts]: any) => [n, pts.map((p: any) => ({ t: p.t, v: p.mem }))]))

  async function saveLimits() {
    setBusy(true)
    try {
      const sl: Record<string, any> = {}
      Object.entries(limits).forEach(([s, l]) => { sl[s] = { cpus: l.cpus ? Number(l.cpus) : null, memory_mb: l.memory_mb ? Number(l.memory_mb) : null } })
      runJob(api.put(`/api/apps/${app.id}/limits`, {
        service_limits: sl, cpu_limit: cpuQ ? Number(cpuQ) : null, mem_limit_mb: memQ ? Number(memQ) : null, disk_quota_mb: diskQ ? Number(diskQ) : null,
      }), 'Áp dụng giới hạn')
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <Section title="Sử dụng theo container" actions={
        <select className="input w-auto py-1 text-xs" value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[1, 6, 24, 72].map((h) => <option key={h} value={h}>{h} giờ</option>)}</select>
      }>
        <div className="grid gap-4 p-4 lg:grid-cols-2">
          <div><div className="mb-1 text-xs font-medium text-slate-500">CPU (% của 1 core)</div><MultiLine series={cpu} format={(v) => `${v.toFixed(0)}%`} /></div>
          <div><div className="mb-1 text-xs font-medium text-slate-500">RAM</div><MultiLine series={mem} format={(v) => fmtBytes(v, 0)} /></div>
        </div>
      </Section>

      <Section title="Cấp phát tài nguyên" actions={!staff && <span className="text-xs text-slate-500">Chỉ admin/operator chỉnh được</span>}>
        <div className="space-y-4 p-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Hạn mức CPU app (core)" hint="Tổng giới hạn các service không được vượt"><input className="input" type="number" step="0.1" disabled={!staff} value={cpuQ} onChange={(e) => setCpuQ(e.target.value)} /></Field>
            <Field label="Hạn mức RAM app (MB)"><input className="input" type="number" disabled={!staff} value={memQ} onChange={(e) => setMemQ(e.target.value)} /></Field>
            <Field label="Hạn mức disk (MB)" hint={`Đang dùng ${fmtMB(app.disk_usage_mb)} — cảnh báo khi vượt`}><input className="input" type="number" disabled={!staff} value={diskQ} onChange={(e) => setDiskQ(e.target.value)} /></Field>
          </div>
          <table className="table">
            <thead><tr><th>Service</th><th>CPU (core)</th><th>RAM (MB)</th></tr></thead>
            <tbody>
              {services.map((s) => (
                <tr key={s}>
                  <td className="font-medium">{s}</td>
                  <td><input className="input w-32" type="number" step="0.1" disabled={!staff} value={limits[s]?.cpus || ''} onChange={(e) => setLimits({ ...limits, [s]: { ...limits[s], cpus: e.target.value } })} placeholder="∞" /></td>
                  <td><input className="input w-32" type="number" disabled={!staff} value={limits[s]?.memory_mb || ''} onChange={(e) => setLimits({ ...limits, [s]: { ...limits[s], memory_mb: e.target.value } })} placeholder="∞" /></td>
                </tr>
              ))}
              {!services.length && <tr><td colSpan={3} className="text-slate-500">Chưa có container — deploy trước để thấy danh sách service</td></tr>}
            </tbody>
          </table>
          {staff && <button className="btn-primary" disabled={busy} onClick={saveLimits}>{busy && <Spinner />}Lưu & áp dụng ngay</button>}
          <div className="text-xs text-slate-500">Áp dụng tức thì bằng <span className="font-mono">docker update</span> và lưu vào compose.th.yml để giữ sau khi deploy lại.</div>
        </div>
      </Section>

      <Section title="Mạng edge (cho proxy truy cập)">
        <div className="space-y-3 p-4">
          <div className="text-sm text-slate-500">Service được chọn sẽ gắn vào mạng <span className="font-mono">th-edge</span> để Caddy proxy tới bằng tên container. Chỉ chọn service web, không chọn database.</div>
          <div className="flex flex-wrap gap-3">
            {services.map((s) => (
              <label key={s} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" disabled={!canDeploy} checked={edge.includes(s)} onChange={(e) => setEdge(e.target.checked ? [...edge, s] : edge.filter((x) => x !== s))} />{s}
              </label>
            ))}
          </div>
          {canDeploy && <button className="btn-secondary" onClick={() => runJob(api.put(`/api/apps/${app.id}/edge`, { services: edge }), 'Gắn mạng edge')}>Lưu</button>}
          {!canDeploy && <div className="text-xs text-slate-500">Cần quyền deployer</div>}
        </div>
      </Section>
    </div>
  )
}
