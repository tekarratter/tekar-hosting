import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Pencil, Trash2, Lock, Shield, Wrench, ShieldAlert, Zap, ArrowRightLeft, Ban, Globe } from 'lucide-react'
import { api, type AccessList, type CustomCert, type HostStats, type Route } from '../api'
import { Badge, ConfirmModal, Loading, StatusBadge, Toggle } from './ui'
import { useToast } from './toast'
import { useIsStaff } from '../auth'
import { fmtAgo } from '../util'

export function SslBadge({ r, certs }: { r: Route; certs?: CustomCert[] }) {
  const o = r.options || {}
  const cert = r.certificate_id ? certs?.find((c) => c.id === r.certificate_id) : null
  let label: string
  let color = 'gray'
  if (r.certificate_id) { label = `cert: ${cert?.name || '#' + r.certificate_id}`; color = 'purple' }
  else if (r.tls_mode === 'auto') { label = `Let's Encrypt ${o.acme_challenge === 'dns' ? 'DNS' : o.acme_challenge === 'http' ? 'HTTP-01' : 'auto'}`; color = 'green' }
  else if (r.tls_mode === 'internal') { label = 'internal'; color = 'yellow' }
  else if (r.tls_mode === 'tunnel') { label = 'CF Tunnel'; color = 'blue' }
  else label = 'HTTP'
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Badge color={color}>{label}</Badge>
      {r.cf_tunnel && r.tls_mode !== 'tunnel' && <Badge color="blue">+tunnel</Badge>}
      {o.force_https && <Badge color="green" title="Force SSL">force</Badge>}
      {o.hsts && <Badge color="green" title={`HSTS${o.hsts_subdomains ? ' + subdomains' : ''}${o.hsts_preload ? ' + preload' : ''}`}>HSTS{o.hsts_subdomains ? '+' : ''}</Badge>}
    </span>
  )
}

export function RouteTable({ routes, loading, canEdit, onEdit, onChanged, hideApp }: {
  routes: Route[]; loading?: boolean; canEdit: boolean; onEdit: (r: Route) => void; onChanged: () => void; hideApp?: boolean
}) {
  const toast = useToast()
  const staff = useIsStaff()
  const [del, setDel] = useState<Route | null>(null)
  const acls = useQuery({ queryKey: ['access-lists'], queryFn: () => api.get<AccessList[]>('/api/proxy/access-lists') })
  const certs = useQuery({ queryKey: ['certificates'], queryFn: () => api.get<{ custom: CustomCert[] }>('/api/proxy/certificates') })
  const stats = useQuery({ queryKey: ['proxy-stats'], queryFn: () => api.get<Record<string, HostStats>>('/api/proxy/stats'), enabled: staff, refetchInterval: 60000 })
  if (loading) return <Loading />

  async function toggle(r: Route, enabled: boolean) {
    try { await api.post(`/api/proxy/routes/${r.id}/toggle`, { enabled }); toast.success(enabled ? 'Đã bật' : 'Đã tắt'); onChanged() } catch (e) { toast.error(e) }
  }

  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead><tr><th>Domain</th>{!hideApp && <th>App</th>}<th>Đích</th><th>SSL</th><th>Bảo vệ</th><th>Health</th>{staff && <th>Traffic</th>}<th>Bật</th><th></th></tr></thead>
        <tbody>
          {routes.map((r) => {
            const o = r.options || {}
            const acl = r.access_list_id ? acls.data?.find((a) => a.id === r.access_list_id) : null
            const st = r.hosts.map((h) => stats.data?.[h]).filter(Boolean) as HostStats[]
            const req = st.reduce((a, s) => a + s.requests, 0)
            const e5 = st.reduce((a, s) => a + s['5xx'], 0)
            const e4 = st.reduce((a, s) => a + s['4xx'], 0)
            const avg = st.length ? Math.round(st.reduce((a, s) => a + s.avg_ms * s.requests, 0) / Math.max(1, req)) : 0
            return (
              <tr key={r.id} className={r.enabled ? '' : 'opacity-50'}>
                <td>
                  {r.hosts.map((h) => <div key={h}><a href={`https://${h.replace('*.', 'www.')}${r.path_prefix || ''}`} target="_blank" rel="noreferrer" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">{h}</a>{r.path_prefix && <span className="font-mono text-xs text-slate-500">{r.path_prefix}</span>}</div>)}
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {o.maintenance && <Badge color="yellow"><Wrench className="mr-0.5 h-3 w-3" />bảo trì</Badge>}
                    {o.path_routes?.length > 0 && <Badge color="purple">{o.path_routes.length} location</Badge>}
                    {o.custom_caddyfile && <Badge color="purple">caddyfile</Badge>}
                    {r.source !== 'manual' && <Badge color={r.source === 'npm-import' ? 'orange' : 'gray'}>{r.source}</Badge>}
                  </div>
                </td>
                {!hideApp && <td className="text-sm">{r.app_name || <span className="text-slate-400">hệ thống</span>}</td>}
                <td className="mono">
                  {r.kind === 'redirect' ? (
                    <span className="flex items-center gap-1"><ArrowRightLeft className="h-3 w-3 text-orange-500" />{o.redirect_code || 301} → {o.redirect_scheme && o.redirect_scheme !== 'auto' && !String(o.redirect_to).includes('://') ? `${o.redirect_scheme}://` : ''}{o.redirect_to}</span>
                  ) : r.kind === 'dead' ? (
                    <span className="flex items-center gap-1"><Ban className="h-3 w-3 text-slate-500" />{o.status_code || 404}</span>
                  ) : (
                    <>
                      {r.upstreams.map((u) => <div key={u} className="flex items-center gap-1"><Globe className="h-3 w-3 text-slate-400" />{u}</div>)}
                      {o.path_routes?.map((p: any) => <div key={p.path} className="text-slate-500">{p.path} → {p.upstreams.join(', ')}</div>)}
                    </>
                  )}
                </td>
                <td className="text-xs"><SslBadge r={r} certs={certs.data?.custom} /></td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {acl && <Badge color="blue"><Lock className="mr-0.5 h-3 w-3" />{acl.name}</Badge>}
                    {o.basic_auth && <Badge color="blue"><Lock className="mr-0.5 h-3 w-3" />auth</Badge>}
                    {o.ip_allow && <Badge color="blue"><Shield className="mr-0.5 h-3 w-3" />IP</Badge>}
                    {o.block_exploits && <Badge color="green" title="Chặn exploit"><ShieldAlert className="h-3 w-3" /></Badge>}
                    {o.cache_assets && <Badge color="gray" title="Cache assets"><Zap className="h-3 w-3" /></Badge>}
                    {!acl && !o.basic_auth && !o.ip_allow && !o.block_exploits && <span className="text-xs text-slate-400">công khai</span>}
                  </div>
                </td>
                <td>{r.kind !== 'proxy' ? <span className="text-xs text-slate-400">—</span> : r.health_status ? <StatusBadge status={r.health_status} /> : '—'}<div className="text-xs text-slate-400">{r.health_checked_at && r.kind === 'proxy' ? fmtAgo(r.health_checked_at) : ''}</div></td>
                {staff && (
                  <td className="whitespace-nowrap text-xs">
                    {req ? <>
                      <div>{req} req · {avg}ms</div>
                      <div>{e4 > 0 && <span className="text-amber-600">{e4} 4xx </span>}{e5 > 0 && <span className="text-rose-600">{e5} 5xx</span>}</div>
                    </> : <span className="text-slate-400">—</span>}
                  </td>
                )}
                <td>{canEdit ? <Toggle checked={r.enabled} onChange={(v) => toggle(r, v)} /> : r.enabled ? 'bật' : 'tắt'}</td>
                <td className="whitespace-nowrap text-right">
                  {canEdit && <>
                    <button className="btn-ghost btn-sm" onClick={() => onEdit(r)}><Pencil className="h-3.5 w-3.5" /></button>
                    <button className="btn-ghost btn-sm" onClick={() => setDel(r)}><Trash2 className="h-3.5 w-3.5" /></button>
                  </>}
                </td>
              </tr>
            )
          })}
          {!routes.length && <tr><td colSpan={9} className="text-center text-slate-500">Chưa có host</td></tr>}
        </tbody>
      </table>
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá host" message={<>Xoá <b>{del?.hosts.join(', ')}</b>?</>}
        onConfirm={async () => { try { await api.del(`/api/proxy/routes/${del!.id}`); toast.success('Đã xoá'); setDel(null); onChanged() } catch (e) { toast.error(e) } }} />
    </div>
  )
}
