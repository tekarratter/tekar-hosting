import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Play, Square, RotateCw, FileText, TerminalSquare, Info } from 'lucide-react'
import { api, type Container } from '../api'
import { Badge, Drawer, Loading, Modal, StatusBadge, Tabs } from './ui'
import { useToast } from './toast'
import LogStream from './LogStream'
import Terminal from './Terminal'
import { fmtAgo, fmtBytes } from '../util'

export function InspectView({ name }: { name: string }) {
  const q = useQuery({ queryKey: ['inspect', name], queryFn: () => api.get(`/api/containers/${encodeURIComponent(name)}`) })
  if (q.isLoading) return <Loading />
  if (!q.data) return null
  const d = q.data
  const row = (k: string, v: React.ReactNode) => <tr><td className="w-40 text-xs font-medium text-slate-500">{k}</td><td className="break-all text-sm">{v}</td></tr>
  return (
    <div className="space-y-4">
      <table className="table"><tbody>
        {row('Image', <span className="font-mono text-xs">{d.image}</span>)}
        {row('Trạng thái', <><StatusBadge status={d.state} /> {d.health && <Badge>{d.health}</Badge>} {d.exit_code ? <Badge color="red">exit {d.exit_code}</Badge> : null}</>)}
        {row('Khởi động', `${fmtAgo(d.started_at)} · restart ${d.restart_count}`)}
        {row('Lệnh', <span className="font-mono text-xs">{JSON.stringify(d.entrypoint)} {JSON.stringify(d.command)}</span>)}
        {row('User', d.user)}
        {row('Restart policy', d.restart_policy)}
        {row('Network', `${d.network_mode} · ${d.networks.join(', ')}`)}
        {row('Giới hạn', `CPU ${d.cpu_limit ?? '∞'} · RAM ${d.mem_limit ? fmtBytes(d.mem_limit) : '∞'}`)}
        {row('Privileged', d.privileged ? <Badge color="red">có</Badge> : 'không')}
        {row('Ports', d.ports.map((p: any) => `${p.host_ip ? `${p.host_ip}:${p.host_port}→` : ''}${p.container}`).join(', ') || '—')}
      </tbody></table>
      <div>
        <div className="mb-1 text-sm font-medium">Mounts</div>
        <table className="table"><tbody>
          {d.mounts.map((m: any, i: number) => <tr key={i}><td><Badge>{m.type}</Badge></td><td className="mono break-all">{m.name || m.source}</td><td className="mono">{m.destination}</td><td>{m.rw ? 'rw' : 'ro'}</td></tr>)}
        </tbody></table>
      </div>
      <div>
        <div className="mb-1 text-sm font-medium">Môi trường (secret đã che)</div>
        <pre className="max-h-72 overflow-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{d.env.join('\n')}</pre>
      </div>
      <div>
        <div className="mb-1 text-sm font-medium">Labels</div>
        <pre className="max-h-48 overflow-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{Object.entries(d.labels).map(([k, v]) => `${k}=${v}`).join('\n')}</pre>
      </div>
    </div>
  )
}

export function ContainerPanel({ name, initial = 'logs', onClose, canExec }: { name: string | null; initial?: string; onClose: () => void; canExec: boolean }) {
  const [tab, setTab] = useState(initial)
  if (!name) return null
  return (
    <Drawer open onClose={onClose} title={name}>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'logs', label: 'Logs' }, { id: 'terminal', label: 'Terminal', hidden: !canExec }, { id: 'inspect', label: 'Chi tiết' }]} />
      {tab === 'logs' && <LogStream container={name} height={560} />}
      {tab === 'terminal' && canExec && <Terminal container={name} height={560} />}
      {tab === 'inspect' && <InspectView name={name} />}
    </Drawer>
  )
}

export default function ContainerTable({ containers, canAct, showProject = false, onChanged }: { containers: Container[]; canAct: boolean; showProject?: boolean; onChanged?: () => void }) {
  const toast = useToast()
  const [panel, setPanel] = useState<{ name: string; tab: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  async function act(name: string, verb: string) {
    setBusy(name + verb)
    try { await api.post(`/api/containers/${encodeURIComponent(name)}/${verb}`); toast.success(`${verb} ${name}`); onChanged?.() } catch (e) { toast.error(e) } finally { setBusy(null) }
  }
  return (
    <>
      <div className="overflow-x-auto">
        <table className="table">
          <thead><tr><th>Container</th>{showProject && <th>Project</th>}<th>Image</th><th>Trạng thái</th><th>Ports</th><th>Giới hạn</th><th className="text-right">Thao tác</th></tr></thead>
          <tbody>
            {containers.map((c) => (
              <tr key={c.id}>
                <td><div className="font-medium">{c.name}</div><div className="text-xs text-slate-500">{c.service || ''}</div></td>
                {showProject && <td className="text-xs">{c.project || '—'}</td>}
                <td className="mono max-w-[220px] truncate" title={c.image}>{c.image}</td>
                <td className="whitespace-nowrap">
                  <StatusBadge status={c.state} /> {c.health && c.health !== 'healthy' && <StatusBadge status={c.health} />}
                  {c.restart_count > 3 && <Badge color="yellow" title="Số lần restart">↻{c.restart_count}</Badge>}
                  <div className="text-xs text-slate-500">{c.state === 'running' ? fmtAgo(c.started_at) : ''}</div>
                </td>
                <td className="mono">{c.ports.filter((p) => p.host_port).map((p) => `${p.host_ip === '0.0.0.0' ? '' : p.host_ip + ':'}${p.host_port}→${p.container.replace('/tcp', '')}`).join(', ') || '—'}</td>
                <td className="whitespace-nowrap text-xs">{c.cpu_limit ? `${c.cpu_limit} CPU` : '—'} · {c.mem_limit ? fmtBytes(c.mem_limit, 0) : '—'}</td>
                <td className="whitespace-nowrap text-right">
                  <button className="btn-ghost btn-sm" title="Logs" onClick={() => setPanel({ name: c.name, tab: 'logs' })}><FileText className="h-3.5 w-3.5" /></button>
                  {canAct && <button className="btn-ghost btn-sm" title="Terminal" onClick={() => setPanel({ name: c.name, tab: 'terminal' })}><TerminalSquare className="h-3.5 w-3.5" /></button>}
                  <button className="btn-ghost btn-sm" title="Chi tiết" onClick={() => setPanel({ name: c.name, tab: 'inspect' })}><Info className="h-3.5 w-3.5" /></button>
                  {canAct && (c.state === 'running'
                    ? <>
                      <button className="btn-ghost btn-sm" title="Restart" disabled={!!busy} onClick={() => act(c.name, 'restart')}><RotateCw className="h-3.5 w-3.5" /></button>
                      <button className="btn-ghost btn-sm" title="Stop" disabled={!!busy} onClick={() => act(c.name, 'stop')}><Square className="h-3.5 w-3.5" /></button>
                    </>
                    : <button className="btn-ghost btn-sm" title="Start" disabled={!!busy} onClick={() => act(c.name, 'start')}><Play className="h-3.5 w-3.5" /></button>)}
                </td>
              </tr>
            ))}
            {!containers.length && <tr><td colSpan={7} className="text-center text-slate-500">Không có container</td></tr>}
          </tbody>
        </table>
      </div>
      {panel && <ContainerPanel name={panel.name} initial={panel.tab} canExec={canAct} onClose={() => setPanel(null)} />}
    </>
  )
}
