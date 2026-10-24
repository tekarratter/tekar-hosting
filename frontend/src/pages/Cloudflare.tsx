import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Cloud, RefreshCw, RotateCw, Trash2, Link2 } from 'lucide-react'
import { api } from '../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, PageHeader, Section, Spinner, Toggle } from '../components/ui'
import { useToast } from '../components/toast'
import { useIsAdmin } from '../auth'

export default function Cloudflare() {
  const toast = useToast()
  const admin = useIsAdmin()
  const st = useQuery({ queryKey: ['cf-status'], queryFn: () => api.get('/api/cloudflare/status'), refetchInterval: 30000 })
  const cfg = st.data?.config
  const tunnel = st.data?.tunnel
  const [token, setToken] = useState('')
  const [verified, setVerified] = useState<any>(null)
  const [account, setAccount] = useState('')
  const [zone, setZone] = useState('')
  const [domain, setDomain] = useState('')
  const [wildcard, setWildcard] = useState(false)
  const [email, setEmail] = useState('')
  const [tname, setTname] = useState('tekar-hosting')
  const [busy, setBusy] = useState<string | null>(null)
  const [showDns, setShowDns] = useState(false)
  const dns = useQuery({ queryKey: ['cf-dns'], queryFn: () => api.get<any[]>('/api/cloudflare/dns'), enabled: showDns && !!cfg?.has_token })
  const [newDns, setNewDns] = useState('')
  const [delRec, setDelRec] = useState<any>(null)

  useEffect(() => {
    if (cfg) { setAccount(cfg.account_id || ''); setZone(cfg.zone_id || ''); setDomain(cfg.base_domain || ''); setWildcard(!!cfg.wildcard_enabled); setEmail(cfg.acme_email || '') }
  }, [cfg?.account_id, cfg?.zone_id, cfg?.base_domain, cfg?.wildcard_enabled]) // eslint-disable-line

  const run = async (k: string, fn: () => Promise<any>, ok?: (r: any) => string) => {
    setBusy(k)
    try { const r = await fn(); toast.success(ok ? ok(r) : 'Xong'); st.refetch(); return r } catch (e) { toast.error(e) } finally { setBusy(null) }
  }

  if (st.isLoading) return <Loading />
  const tunnelTarget = tunnel?.tunnel_id ? `${tunnel.tunnel_id}.cfargotunnel.com` : ''

  return (
    <div className="space-y-4">
      <PageHeader title="Cloudflare" subtitle="Tunnel + DNS + wildcard domain — public dịch vụ mà không cần mở cổng" />
      <ErrorBox error={st.error} />

      <Section title={<span className="flex items-center gap-2">1. API token {cfg?.has_token && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}</span>}>
        <div className="space-y-3 p-4 text-sm">
          <div className="text-slate-500">Tạo token tại dash.cloudflare.com → My Profile → API Tokens với quyền: <b>Account › Cloudflare Tunnel: Edit</b>, <b>Zone › DNS: Edit</b>, <b>Zone › Zone: Read</b>.</div>
          {admin && (
            <div className="flex gap-2">
              <input className="input font-mono" type="password" placeholder={cfg?.has_token ? 'Đã lưu token — nhập để thay' : 'API token'} value={token} onChange={(e) => setToken(e.target.value)} />
              <button className="btn-secondary" disabled={!token || busy === 'verify'} onClick={async () => {
                const r = await run('verify', () => api.post('/api/cloudflare/verify', { api_token: token }), (r) => `Token hợp lệ · ${r.zones.length} zone`)
                if (r) { setVerified(r); if (!account && r.accounts[0]) setAccount(r.accounts[0].id) }
              }}>{busy === 'verify' && <Spinner />}Kiểm tra</button>
            </div>
          )}
        </div>
      </Section>

      <Section title="2. Tài khoản, zone & domain gốc">
        <div className="space-y-3 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Account">
              {verified ? (
                <select className="input" value={account} onChange={(e) => setAccount(e.target.value)}>{verified.accounts.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
              ) : <input className="input font-mono" value={account} onChange={(e) => setAccount(e.target.value)} disabled={!admin} />}
            </Field>
            <Field label="Zone">
              {verified ? (
                <select className="input" value={zone} onChange={(e) => { setZone(e.target.value); const z = verified.zones.find((z: any) => z.id === e.target.value); if (z && !domain) setDomain(z.name) }}>
                  <option value="">— chọn —</option>
                  {verified.zones.filter((z: any) => !account || z.account?.id === account).map((z: any) => <option key={z.id} value={z.id}>{z.name}</option>)}
                </select>
              ) : <input className="input font-mono" value={zone} onChange={(e) => setZone(e.target.value)} disabled={!admin} />}
            </Field>
            <Field label="Domain gốc" hint="vd apps.example.com hoặc example.com"><input className="input font-mono" value={domain} disabled={!admin} onChange={(e) => setDomain(e.target.value)} /></Field>
            <Field label="Email ACME (Let's Encrypt)"><input className="input" value={email} disabled={!admin} onChange={(e) => setEmail(e.target.value)} /></Field>
          </div>
          <Toggle checked={wildcard} onChange={(v) => admin && setWildcard(v)} label={<>Bật wildcard <span className="font-mono">*.{domain || 'domain'}</span> → mọi subdomain đi qua tunnel tới Caddy (không cần tạo DNS từng host) + cert wildcard qua DNS-01</>} />
          {admin && <button className="btn-primary" disabled={!account || !zone || !domain || busy === 'save'} onClick={() => run('save', () => api.post('/api/cloudflare/config', {
            api_token: token || null, account_id: account, zone_id: zone, base_domain: domain, wildcard_enabled: wildcard, acme_email: email,
          }), (r) => `Đã lưu · Caddy: ${r.apply?.caddy}${r.apply?.tunnel ? ` · Tunnel: ${r.apply.tunnel}` : ''}`)}>{busy === 'save' && <Spinner />}Lưu cấu hình</button>}
        </div>
      </Section>

      <Section title={<span className="flex items-center gap-2"><Cloud className="h-4 w-4" />3. Tunnel</span>} actions={tunnel?.tunnel_id && <>
        <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => run('sync', () => api.post('/api/cloudflare/tunnel/sync'), (r) => `Đã đồng bộ ${r.ingress.length - 1} host · DNS: ${r.dns.length}${r.dns_errors.length ? ` · lỗi: ${r.dns_errors.join('; ')}` : ''}`)}><RefreshCw className="h-3.5 w-3.5" />Đồng bộ ingress + DNS</button>
        <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => run('restart', () => api.post('/api/cloudflare/tunnel/restart'), () => 'Đã khởi động lại connector')}><RotateCw className="h-3.5 w-3.5" />Restart connector</button>
      </>}>
        <div className="space-y-3 p-4 text-sm">
          {tunnel?.tunnel_id ? (
            <div className="grid gap-2 md:grid-cols-4">
              <div><div className="label">Tunnel</div><div className="font-medium">{tunnel.name || '—'}</div><div className="font-mono text-xs text-slate-500">{tunnel.tunnel_id}</div></div>
              <div><div className="label">Trạng thái (Cloudflare)</div>{tunnel.status ? <Badge color={tunnel.status === 'healthy' ? 'green' : tunnel.status === 'degraded' ? 'yellow' : 'red'}>{tunnel.status}</Badge> : '—'} <span className="text-xs text-slate-500">{tunnel.connections ?? 0} kết nối</span></div>
              <div><div className="label">Connector (th-cloudflared)</div>{tunnel.connector ? <Badge color={tunnel.connector.state === 'running' ? 'green' : 'red'}>{tunnel.connector.state}</Badge> : <Badge color="red">chưa chạy</Badge>}</div>
              <div><div className="label">CNAME đích</div><div className="font-mono text-xs break-all">{tunnelTarget}</div></div>
            </div>
          ) : <div className="text-slate-500">Chưa có tunnel. Tunnel do panel quản lý (remote-managed); tunnel cũ trong /data/application/cloudflared vẫn chạy độc lập.</div>}
          {tunnel?.error && <ErrorBox error={tunnel.error} />}
          {admin && (
            <div className="flex gap-2">
              <input className="input w-64" value={tname} onChange={(e) => setTname(e.target.value)} />
              <button className="btn-primary" disabled={!cfg?.has_token || !!busy} onClick={() => run('tunnel', () => api.post('/api/cloudflare/tunnel', { name: tname }), (r) => `Tunnel ${r.name} sẵn sàng`)}>{busy === 'tunnel' && <Spinner />}{tunnel?.tunnel_id ? 'Kết nối lại / tạo tunnel' : 'Tạo & chạy tunnel'}</button>
            </div>
          )}
          {tunnel?.ingress?.length > 0 && (
            <table className="table">
              <thead><tr><th>Hostname</th><th>Service</th></tr></thead>
              <tbody>{tunnel.ingress.map((r: any, i: number) => <tr key={i}><td className="mono">{r.hostname || '(mặc định)'}</td><td className="mono">{r.service}</td></tr>)}</tbody>
            </table>
          )}
        </div>
      </Section>

      <Section title="DNS records" actions={<button className="btn-secondary btn-sm" onClick={() => { setShowDns(true); dns.refetch() }}>{showDns ? 'Tải lại' : 'Hiện'}</button>}>
        {showDns && (
          <div className="p-4">
            {tunnel?.tunnel_id && (
              <div className="mb-3 flex gap-2">
                <input className="input font-mono" placeholder="sub.example.com" value={newDns} onChange={(e) => setNewDns(e.target.value)} />
                <button className="btn-secondary" disabled={!newDns || !!busy} onClick={async () => { await run('dns', () => api.post('/api/cloudflare/dns/tunnel', { name: newDns }), () => `${newDns} → tunnel`); setNewDns(''); dns.refetch() }}><Link2 className="h-4 w-4" />Trỏ về tunnel</button>
              </div>
            )}
            {dns.isLoading && <Loading />}
            <ErrorBox error={dns.error} />
            <table className="table">
              <thead><tr><th>Tên</th><th>Loại</th><th>Nội dung</th><th>Proxy</th><th></th></tr></thead>
              <tbody>
                {(dns.data || []).map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.name}</td><td><Badge>{r.type}</Badge></td>
                    <td className="mono max-w-xs truncate" title={r.content}>{r.content}{r.content === tunnelTarget && <Badge color="green">tunnel này</Badge>}</td>
                    <td>{r.proxied ? <Badge color="orange">proxied</Badge> : 'DNS only'}</td>
                    <td className="whitespace-nowrap text-right">
                      {tunnel?.tunnel_id && r.content !== tunnelTarget && ['A', 'AAAA', 'CNAME'].includes(r.type) && r.type === 'CNAME' && (
                        <button className="btn-ghost btn-sm" title="Trỏ về tunnel" onClick={async () => { await run('dns', () => api.post('/api/cloudflare/dns/tunnel', { name: r.name })); dns.refetch() }}><Link2 className="h-3.5 w-3.5" /></button>
                      )}
                      {admin && <button className="btn-ghost btn-sm" onClick={() => setDelRec(r)}><Trash2 className="h-3.5 w-3.5" /></button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <ConfirmModal open={!!delRec} onClose={() => setDelRec(null)} title="Xoá DNS record" requireText={delRec?.name}
        message={<>Xoá <b>{delRec?.type}</b> {delRec?.name} → {delRec?.content}</>}
        onConfirm={async () => { try { await api.del(`/api/cloudflare/dns/${delRec.id}`); toast.success('Đã xoá'); setDelRec(null); dns.refetch() } catch (e) { toast.error(e) } }} />
    </div>
  )
}
