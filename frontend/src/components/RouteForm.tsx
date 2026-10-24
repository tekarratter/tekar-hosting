import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Trash2, CheckCircle2, AlertTriangle } from 'lucide-react'
import { api, type AccessList, type AppT, type CustomCert, type Route, type RouteKind } from '../api'
import { Field, Modal, Tabs, Toggle, Spinner } from './ui'
import { useToast } from './toast'
import { useIsStaff } from '../auth'

interface Candidate { container: string; project: string | null; ports: number[]; on_edge: boolean }

function headersToText(h?: Record<string, string>) {
  return Object.entries(h || {}).map(([k, v]) => `${k}: ${v}`).join('\n')
}
function textToHeaders(t: string): Record<string, string> {
  const out: Record<string, string> = {}
  t.split('\n').forEach((l) => { const i = l.indexOf(':'); if (i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1).trim() })
  return out
}
const lines = (t: string) => t.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)

interface PathRoute { path: string; upstreams: string; strip_prefix: boolean; custom_caddyfile: string; showCf: boolean }

function CaddyfileCheck({ value }: { value: string }) {
  const [res, setRes] = useState<{ ok: boolean; error?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      <button type="button" className="btn-secondary btn-sm" disabled={!value.trim() || busy} onClick={async () => {
        setBusy(true)
        try { setRes(await api.post('/api/proxy/adapt', { caddyfile: value })) } catch (e: any) { setRes({ ok: false, error: e.message }) } finally { setBusy(false) }
      }}>{busy && <Spinner />}Kiểm tra</button>
      {res && (res.ok
        ? <span className="flex items-center gap-1 text-xs text-emerald-600"><CheckCircle2 className="h-3.5 w-3.5" />Hợp lệ</span>
        : <span className="flex items-center gap-1 text-xs text-rose-600"><AlertTriangle className="h-3.5 w-3.5" />{res.error}</span>)}
    </div>
  )
}

export default function RouteForm({ open, onClose, route, appId, onSaved }: {
  open: boolean; onClose: () => void; route?: Route | null; appId?: number | null; onSaved?: (r: any) => void
}) {
  const toast = useToast()
  const staff = useIsStaff()
  const o = route?.options || {}
  const [tab, setTab] = useState('details')
  const [kind, setKind] = useState<RouteKind>(route?.kind || 'proxy')
  const [hosts, setHosts] = useState((route?.hosts || []).join('\n'))
  const [pathPrefix, setPathPrefix] = useState(route?.path_prefix || '')
  const [upstreams, setUpstreams] = useState((route?.upstreams || []).join('\n'))
  const [tls, setTls] = useState(route?.tls_mode || 'tunnel')
  const [cfTunnel, setCfTunnel] = useState(route?.cf_tunnel ?? true)
  const [enabled, setEnabled] = useState(route?.enabled ?? true)
  const [priority, setPriority] = useState(route?.priority ?? 0)
  const [healthPath, setHealthPath] = useState(route?.health_path || '')
  const [note, setNote] = useState(route?.note || '')
  const [app, setApp] = useState<number | null>(route ? route.app_id : appId ?? null)
  const [aclId, setAclId] = useState<number | null>(route?.access_list_id ?? null)
  const [certId, setCertId] = useState<number | null>(route?.certificate_id ?? null)
  // chi tiết
  const [blockExploits, setBlockExploits] = useState(!!o.block_exploits)
  const [cacheAssets, setCacheAssets] = useState(!!o.cache_assets)
  const [stripPrefix, setStripPrefix] = useState(!!o.strip_prefix)
  const [redirectTo, setRedirectTo] = useState(o.redirect_to || '')
  const [redirectScheme, setRedirectScheme] = useState(o.redirect_scheme || 'auto')
  const [redirectCode, setRedirectCode] = useState(o.redirect_code || 301)
  const [redirectKeep, setRedirectKeep] = useState(o.redirect_keep_path ?? true)
  const [deadStatus, setDeadStatus] = useState<string>(o.status_code ? String(o.status_code) : '404')
  const [deadBody, setDeadBody] = useState(o.body || '')
  // ssl
  const [acme, setAcme] = useState(o.acme_challenge || 'auto')
  const [forceHttps, setForceHttps] = useState(!!o.force_https)
  const [hsts, setHsts] = useState(!!o.hsts)
  const [hstsSub, setHstsSub] = useState(!!o.hsts_subdomains)
  const [hstsPreload, setHstsPreload] = useState(!!o.hsts_preload)
  // nâng cao
  const [caddyfile, setCaddyfile] = useState(o.custom_caddyfile || '')
  const [ipAllow, setIpAllow] = useState((o.ip_allow || []).join('\n'))
  const [auth, setAuth] = useState<{ username: string; password: string }[]>((o.basic_auth || []).map((a: any) => ({ username: a.username, password: '' })))
  const [maintenance, setMaintenance] = useState(!!o.maintenance)
  const [maintHtml, setMaintHtml] = useState(o.maintenance_html || '')
  const [maxBody, setMaxBody] = useState<string>(o.max_body_mb ? String(o.max_body_mb) : '')
  const [timeout, setTimeoutS] = useState<string>(o.timeout_s ? String(o.timeout_s) : '')
  const [secHeaders, setSecHeaders] = useState(o.security_headers ?? true)
  const [gzip, setGzip] = useState(o.gzip ?? true)
  const [cors, setCors] = useState(o.cors_origins || '')
  const [headersUp, setHeadersUp] = useState(headersToText(o.headers_up))
  const [headersDown, setHeadersDown] = useState(headersToText(o.headers_down))
  const [hideHeaders, setHideHeaders] = useState((o.hide_headers || []).join(', '))
  const [hostHeader, setHostHeader] = useState(o.host_header || '')
  const [lb, setLb] = useState(o.lb_policy || 'round_robin')
  const [healthUri, setHealthUri] = useState(o.health_uri || '')
  const [insecure, setInsecure] = useState(!!o.upstream_insecure)
  const [sni, setSni] = useState(o.upstream_sni || '')
  const [flush, setFlush] = useState(o.flush_interval != null)
  const [pathRoutes, setPathRoutes] = useState<PathRoute[]>((o.path_routes || []).map((p: any) => ({
    path: p.path, upstreams: (p.upstreams || []).join(', '), strip_prefix: !!p.strip_prefix, custom_caddyfile: p.custom_caddyfile || '', showCf: !!p.custom_caddyfile,
  })))
  const [busy, setBusy] = useState(false)

  const cands = useQuery({ queryKey: ['upstream-candidates'], queryFn: () => api.get<Candidate[]>('/api/proxy/upstream-candidates'), enabled: open })
  const apps = useQuery({ queryKey: ['apps'], queryFn: () => api.get<AppT[]>('/api/apps'), enabled: open && appId == null })
  const acls = useQuery({ queryKey: ['access-lists'], queryFn: () => api.get<AccessList[]>('/api/proxy/access-lists'), enabled: open })
  const certs = useQuery({ queryKey: ['certificates'], queryFn: () => api.get<{ custom: CustomCert[] }>('/api/proxy/certificates'), enabled: open && staff })
  const options = useMemo(() => (cands.data || []).flatMap((c) => (c.ports.length ? c.ports : [80]).map((p) => `${c.container}:${p}`)), [cands.data])

  async function save() {
    const isProxy = kind === 'proxy'
    const validPR = pathRoutes.filter((p) => p.path && p.upstreams)
    const opts: Record<string, any> = {
      block_exploits: blockExploits || undefined,
      cache_assets: isProxy && cacheAssets ? true : undefined,
      strip_prefix: isProxy && stripPrefix ? true : undefined,
      redirect_to: kind === 'redirect' ? redirectTo : undefined,
      redirect_scheme: kind === 'redirect' ? redirectScheme : undefined,
      redirect_code: kind === 'redirect' ? Number(redirectCode) : undefined,
      redirect_keep_path: kind === 'redirect' ? redirectKeep : undefined,
      status_code: kind === 'dead' && deadStatus !== '404' ? Number(deadStatus) : undefined,
      body: kind === 'dead' && deadBody ? deadBody : undefined,
      acme_challenge: tls === 'auto' && acme !== 'auto' ? acme : undefined,
      force_https: forceHttps || undefined,
      hsts: hsts || undefined,
      hsts_subdomains: hsts && hstsSub ? true : undefined,
      hsts_preload: hsts && hstsPreload ? true : undefined,
      custom_caddyfile: isProxy && caddyfile.trim() ? caddyfile : undefined,
      ip_allow: lines(ipAllow).length ? lines(ipAllow) : undefined,
      basic_auth: auth.filter((a) => a.username).length ? auth.filter((a) => a.username) : undefined,
      maintenance: maintenance || undefined,
      maintenance_html: maintenance && maintHtml ? maintHtml : undefined,
      max_body_mb: isProxy && maxBody ? Number(maxBody) : undefined,
      timeout_s: isProxy && timeout ? Number(timeout) : undefined,
      security_headers: secHeaders,
      gzip,
      cors_origins: cors || undefined,
      headers_up: Object.keys(textToHeaders(headersUp)).length ? textToHeaders(headersUp) : undefined,
      headers_down: Object.keys(textToHeaders(headersDown)).length ? textToHeaders(headersDown) : undefined,
      hide_headers: lines(hideHeaders).length ? lines(hideHeaders) : undefined,
      host_header: hostHeader || undefined,
      lb_policy: lines(upstreams).length > 1 ? lb : undefined,
      health_uri: healthUri || undefined,
      upstream_insecure: insecure || undefined,
      upstream_sni: sni || undefined,
      flush_interval: flush ? -1 : undefined,
      path_routes: isProxy && validPR.length
        ? validPR.map((p) => ({ path: p.path, upstreams: lines(p.upstreams), strip_prefix: p.strip_prefix, ...(p.custom_caddyfile.trim() ? { custom_caddyfile: p.custom_caddyfile } : {}) }))
        : undefined,
      npm_advanced_config: o.npm_advanced_config || undefined,
    }
    Object.keys(opts).forEach((k) => opts[k] === undefined && delete opts[k])
    const body = {
      app_id: app, kind, hosts: lines(hosts), path_prefix: pathPrefix, upstreams: isProxy ? lines(upstreams) : [], tls_mode: tls, cf_tunnel: cfTunnel,
      enabled, priority: Number(priority) || 0, access_list_id: aclId, certificate_id: certId, options: opts, health_path: healthPath || null, note,
    }
    setBusy(true)
    try {
      const r = route ? await api.put(`/api/proxy/routes/${route.id}`, body) : await api.post('/api/proxy/routes', body)
      const ap = r.apply || {}
      const problems = [ap.caddy !== 'ok' && ap.caddy && `Caddy: ${ap.caddy}`, ap.tunnel && ap.tunnel !== 'ok' && `Tunnel: ${ap.tunnel}`, ...(ap.dns_errors || [])].filter(Boolean)
      if (problems.length) toast.error(`Đã lưu nhưng: ${problems.join('\n')}`)
      else toast.success(`Đã lưu${r.attached?.length ? ` · gắn mạng edge: ${r.attached.join(', ')}` : ''}`)
      onSaved?.(r)
      onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  const upstreamPicker = options.length > 0 && (
    <select className="input mt-1" value="" onChange={(e) => e.target.value && setUpstreams((u) => (u.trim() ? u.trim() + '\n' : '') + e.target.value)}>
      <option value="">+ Chọn nhanh container:port…</option>
      {(cands.data || []).map((c) => (c.ports.length ? c.ports : [80]).map((p) => (
        <option key={`${c.container}:${p}`} value={`${c.container}:${p}`}>{c.container}:{p}{c.on_edge ? '' : ' (sẽ gắn mạng edge)'}</option>
      )))}
    </select>
  )

  return (
    <Modal open={open} onClose={onClose} title={route ? `Sửa host #${route.id}` : 'Thêm host'} size="lg" footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu & áp dụng</button>
    </>}>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'details', label: 'Chi tiết' },
        { id: 'locations', label: `Custom locations${pathRoutes.length ? ` (${pathRoutes.length})` : ''}`, hidden: kind !== 'proxy' },
        { id: 'ssl', label: 'SSL' },
        { id: 'advanced', label: 'Nâng cao' },
      ]} />

      {tab === 'details' && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {([['proxy', 'Proxy host'], ['redirect', 'Redirection'], ['dead', '404 host']] as [RouteKind, string][]).map(([k, l]) => (
              <button key={k} type="button" onClick={() => setKind(k)} className={kind === k ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}>{l}</button>
            ))}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Domain (mỗi dòng 1, hỗ trợ *.domain)">
              <textarea className="input font-mono" rows={3} value={hosts} onChange={(e) => setHosts(e.target.value)} placeholder="app.example.com" />
            </Field>
            {kind === 'proxy' && (
              <Field label="Upstream (mỗi dòng 1, nhiều dòng = load balance)" hint="container:port · host:port (cổng trên host) · http(s)://url">
                <textarea className="input font-mono" rows={3} value={upstreams} onChange={(e) => setUpstreams(e.target.value)} placeholder="myapp-web-1:80" />
                {upstreamPicker}
              </Field>
            )}
            {kind === 'redirect' && (
              <div className="space-y-2">
                <Field label="Chuyển tới (domain hoặc URL)">
                  <input className="input font-mono" value={redirectTo} onChange={(e) => setRedirectTo(e.target.value)} placeholder="new.example.com" />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Field label="Scheme">
                    <select className="input" value={redirectScheme} onChange={(e) => setRedirectScheme(e.target.value)}>
                      <option value="auto">auto (giữ scheme)</option><option value="https">https</option><option value="http">http</option>
                    </select>
                  </Field>
                  <Field label="Mã HTTP">
                    <select className="input" value={redirectCode} onChange={(e) => setRedirectCode(Number(e.target.value))}>
                      {[301, 302, 307, 308].map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </Field>
                </div>
                <Toggle checked={redirectKeep} onChange={setRedirectKeep} label="Giữ path & query" />
              </div>
            )}
            {kind === 'dead' && (
              <div className="space-y-2">
                <Field label="Mã trả về"><input className="input" type="number" value={deadStatus} onChange={(e) => setDeadStatus(e.target.value)} /></Field>
                <Field label="Nội dung (tuỳ chọn)"><input className="input" value={deadBody} onChange={(e) => setDeadBody(e.target.value)} placeholder="Not Found" /></Field>
              </div>
            )}
            {kind === 'proxy' && (
              <Field label="Path prefix (tuỳ chọn)" hint="vd /api — để trống = toàn bộ domain">
                <div className="flex items-center gap-2">
                  <input className="input font-mono" value={pathPrefix} onChange={(e) => setPathPrefix(e.target.value)} placeholder="/" />
                  {pathPrefix && <Toggle checked={stripPrefix} onChange={setStripPrefix} label="strip" />}
                </div>
              </Field>
            )}
            <Field label="Access list" hint="Quản lý ở tab Access lists của trang Proxy">
              <select className="input" value={aclId ?? ''} onChange={(e) => setAclId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Công khai (không giới hạn)</option>
                {(acls.data || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
            {appId == null && (
              <Field label="Gắn với ứng dụng">
                <select className="input" value={app ?? ''} onChange={(e) => setApp(e.target.value ? Number(e.target.value) : null)}>
                  {staff && <option value="">— Không gắn (host hệ thống) —</option>}
                  {(apps.data || []).filter((a) => a.my_role === 'owner' || a.my_role === 'deployer').map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </Field>
            )}
            <Field label="Ghi chú"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          </div>
          <div className="flex flex-wrap gap-4">
            <Toggle checked={enabled} onChange={setEnabled} label="Bật" />
            <Toggle checked={blockExploits} onChange={setBlockExploits} label="Chặn exploit phổ biến" />
            {kind === 'proxy' && <Toggle checked={cacheAssets} onChange={setCacheAssets} label="Cache assets (css/js/ảnh)" />}
            {kind === 'proxy' && <Toggle checked={maintenance} onChange={setMaintenance} label="Bảo trì (503)" />}
          </div>
          {kind === 'proxy' && <div className="text-xs text-slate-500">WebSocket luôn bật, không cần cấu hình.</div>}
        </div>
      )}

      {tab === 'locations' && kind === 'proxy' && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <div className="text-xs text-slate-500">Định tuyến theo path tới upstream khác. Ví dụ /api/* → backend:8000; phần còn lại đi tới upstream chính.</div>
            <button className="btn-secondary btn-sm" onClick={() => setPathRoutes([...pathRoutes, { path: '/api/*', upstreams: '', strip_prefix: false, custom_caddyfile: '', showCf: false }])}><Plus className="h-3.5 w-3.5" />Thêm location</button>
          </div>
          {pathRoutes.map((p, i) => {
            const upd = (patch: Partial<PathRoute>) => setPathRoutes(pathRoutes.map((x, j) => j === i ? { ...x, ...patch } : x))
            return (
              <div key={i} className="mb-2 rounded-md border border-slate-200 p-2 dark:border-slate-800">
                <div className="flex flex-wrap items-center gap-2">
                  <input className="input w-40 font-mono" value={p.path} onChange={(e) => upd({ path: e.target.value })} placeholder="/api/*" />
                  <span className="text-slate-400">→</span>
                  <input className="input flex-1 font-mono" list="th-upstreams" value={p.upstreams} onChange={(e) => upd({ upstreams: e.target.value })} placeholder="backend-1:8000" />
                  <Toggle checked={p.strip_prefix} onChange={(v) => upd({ strip_prefix: v })} label="strip" />
                  <button className="btn-ghost btn-sm" onClick={() => upd({ showCf: !p.showCf })}>Caddyfile</button>
                  <button className="btn-ghost btn-sm" onClick={() => setPathRoutes(pathRoutes.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
                {p.showCf && (
                  <div className="mt-2">
                    <textarea className="input font-mono" rows={3} value={p.custom_caddyfile} onChange={(e) => upd({ custom_caddyfile: e.target.value })} placeholder={'header X-Location "api"'} />
                    <CaddyfileCheck value={p.custom_caddyfile} />
                  </div>
                )}
              </div>
            )
          })}
          {!pathRoutes.length && <div className="p-4 text-center text-sm text-slate-500">Chưa có custom location</div>}
          <datalist id="th-upstreams">{options.map((x) => <option key={x} value={x} />)}</datalist>
        </div>
      )}

      {tab === 'ssl' && (
        <div className="space-y-3">
          <Field label="Chế độ TLS">
            <select className="input" value={tls} onChange={(e) => setTls(e.target.value)}>
              <option value="tunnel">Cloudflare Tunnel (HTTP nội bộ, TLS tại Cloudflare)</option>
              <option value="auto">Let's Encrypt tự động (Caddy cấp & gia hạn)</option>
              <option value="internal">Cert nội bộ (self-signed)</option>
              <option value="off">Chỉ HTTP</option>
            </select>
          </Field>
          {tls === 'auto' && (
            <Field label="Cách xác thực ACME" hint="HTTP-01 cần Caddy giữ cổng 80/443 public (sau khi chuyển đổi khỏi NPM). DNS-01 dùng Cloudflare API token, cần cho wildcard.">
              <select className="input" value={acme} onChange={(e) => setAcme(e.target.value)}>
                <option value="auto">Tự động (DNS-01 nếu domain thuộc zone Cloudflare, ngược lại HTTP-01)</option>
                <option value="http">HTTP-01 / TLS-ALPN</option>
                <option value="dns">DNS-01 (Cloudflare)</option>
              </select>
            </Field>
          )}
          {staff && (
            <Field label="Chứng chỉ tải lên (thay cho ACME)" hint="Quản lý ở tab Chứng chỉ">
              <select className="input" value={certId ?? ''} onChange={(e) => setCertId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— Không dùng —</option>
                {(certs.data?.custom || []).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.domains.join(', ')})</option>)}
              </select>
            </Field>
          )}
          <div className="flex flex-wrap gap-4">
            <Toggle checked={cfTunnel} onChange={setCfTunnel} label="Đi qua Cloudflare Tunnel (tự tạo DNS)" />
            <Toggle checked={forceHttps} onChange={setForceHttps} label="Force SSL (chuyển HTTP → HTTPS)" />
            <Toggle checked={hsts} onChange={setHsts} label="HSTS" />
            {hsts && <Toggle checked={hstsSub} onChange={setHstsSub} label="HSTS subdomains" />}
            {hsts && <Toggle checked={hstsPreload} onChange={setHstsPreload} label="HSTS preload" />}
          </div>
          <div className="text-xs text-slate-500">HTTP/2 và HTTP/3 luôn bật. Force SSL qua tunnel dựa trên header X-Forwarded-Proto của Cloudflare.</div>
        </div>
      )}

      {tab === 'advanced' && (
        <div className="space-y-4">
          {kind === 'proxy' && (
            <Field label="Cấu hình nâng cao (Caddyfile, các directive trong site block)" hint="Tương đương 'Advanced' của NPM. Ví dụ: header X-Frame-Options DENY · @old path /old/* · redir @old /new 301">
              <textarea className="input font-mono" rows={5} value={caddyfile} onChange={(e) => setCaddyfile(e.target.value)} />
              <CaddyfileCheck value={caddyfile} />
            </Field>
          )}
          {o.npm_advanced_config && (
            <Field label="Cấu hình nginx gốc từ NPM (tham khảo, không được áp dụng)">
              <pre className="max-h-40 overflow-auto rounded-md bg-slate-100 p-2 font-mono text-xs dark:bg-slate-800">{o.npm_advanced_config}</pre>
            </Field>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Request header tới upstream (Key: Value)"><textarea className="input font-mono" rows={2} value={headersUp} onChange={(e) => setHeadersUp(e.target.value)} /></Field>
            <Field label="Response header thêm (Key: Value)"><textarea className="input font-mono" rows={2} value={headersDown} onChange={(e) => setHeadersDown(e.target.value)} /></Field>
            <Field label="Ẩn response header"><input className="input font-mono" value={hideHeaders} onChange={(e) => setHideHeaders(e.target.value)} placeholder="Server, X-Powered-By" /></Field>
            <Field label="CORS Allow-Origin"><input className="input" value={cors} onChange={(e) => setCors(e.target.value)} placeholder="* hoặc https://app.com" /></Field>
            {kind === 'proxy' && <>
              <Field label="Giới hạn body (MB)"><input className="input" type="number" value={maxBody} onChange={(e) => setMaxBody(e.target.value)} /></Field>
              <Field label="Timeout upstream (giây)"><input className="input" type="number" value={timeout} onChange={(e) => setTimeoutS(e.target.value)} /></Field>
              <Field label="Load balancing">
                <select className="input" value={lb} onChange={(e) => setLb(e.target.value)}>
                  {['round_robin', 'least_conn', 'ip_hash', 'first', 'random'].map((x) => <option key={x}>{x}</option>)}
                </select>
              </Field>
              <Field label="Active health check URI (Caddy)"><input className="input font-mono" value={healthUri} onChange={(e) => setHealthUri(e.target.value)} placeholder="/healthz" /></Field>
              <Field label="Host header tới upstream" hint="trống = giữ host gốc; 'upstream' = host của upstream"><input className="input font-mono" value={hostHeader} onChange={(e) => setHostHeader(e.target.value)} /></Field>
              <Field label="SNI upstream HTTPS"><input className="input font-mono" value={sni} onChange={(e) => setSni(e.target.value)} /></Field>
              <Field label="Health path (panel giám sát)"><input className="input font-mono" value={healthPath} onChange={(e) => setHealthPath(e.target.value)} placeholder="/" /></Field>
            </>}
            <Field label="Ưu tiên (cao hơn khớp trước)"><input className="input" type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} /></Field>
            <Field label="IP allowlist riêng (CIDR, mỗi dòng 1)" hint="Nên dùng Access list thay thế"><textarea className="input font-mono" rows={2} value={ipAllow} onChange={(e) => setIpAllow(e.target.value)} placeholder="1.2.3.4/32" /></Field>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="label">Basic Auth riêng</label>
                <button className="btn-secondary btn-sm" onClick={() => setAuth([...auth, { username: '', password: '' }])}><Plus className="h-3.5 w-3.5" /></button>
              </div>
              {auth.map((a, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <input className="input" placeholder="user" value={a.username} onChange={(e) => setAuth(auth.map((x, j) => j === i ? { ...x, username: e.target.value } : x))} />
                  <input className="input" type="password" placeholder={route ? '(giữ nguyên)' : 'mật khẩu'} value={a.password} onChange={(e) => setAuth(auth.map((x, j) => j === i ? { ...x, password: e.target.value } : x))} />
                  <button className="btn-ghost btn-sm" onClick={() => setAuth(auth.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap gap-4">
            <Toggle checked={secHeaders} onChange={setSecHeaders} label="Security headers" />
            <Toggle checked={gzip} onChange={setGzip} label="Nén gzip/zstd" />
            {kind === 'proxy' && <Toggle checked={insecure} onChange={setInsecure} label="Bỏ kiểm tra TLS upstream" />}
            {kind === 'proxy' && <Toggle checked={flush} onChange={setFlush} label="Streaming/SSE (flush ngay)" />}
            {kind === 'proxy' && <Toggle checked={maintenance} onChange={setMaintenance} label="Bảo trì (503)" />}
          </div>
          {maintenance && (
            <Field label="HTML trang bảo trì (tuỳ chọn)"><textarea className="input font-mono" rows={3} value={maintHtml} onChange={(e) => setMaintHtml(e.target.value)} /></Field>
          )}
        </div>
      )}
    </Modal>
  )
}
