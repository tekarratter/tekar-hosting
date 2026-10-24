import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { api } from '../../api'
import { Badge, ErrorBox, Loading, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'

export default function NpmImport({ onDone, onJob }: { onDone: () => void; onJob: (id: number, title: string) => void }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['npm-import'], queryFn: () => api.get('/api/proxy/npm-import') })
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [mode, setMode] = useState<'direct' | 'tunnel'>('direct')
  const [acl, setAcl] = useState(true)
  const [def, setDef] = useState(true)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const d = q.data
  const all: { key: string; already: boolean }[] = d ? [...d.items, ...d.streams] : []
  const toggle = (k: string, v: boolean) => { const n = new Set(sel); v ? n.add(k) : n.delete(k); setSel(n) }

  async function run() {
    setBusy(true)
    try {
      const r = await api.post('/api/proxy/npm-import', { keys: [...sel], mode, import_access_lists: acl, import_default_site: def })
      setResult(r); setSel(new Set()); onDone(); q.refetch()
      if (r.stream_job_id) onJob(r.stream_job_id, 'Publish cổng stream')
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  return (
    <div className="space-y-4">
      {result && (
        <Section title="Kết quả import">
          <div className="space-y-2 p-4 text-sm">
            <div className="font-medium text-emerald-600">Đã tạo {result.created.length} mục.</div>
            <ul className="list-disc pl-5 text-xs">{result.created.map((c: any, i: number) => <li key={i}>{Array.isArray(c) ? c.join(', ') : c}</li>)}</ul>
            {result.notes?.length > 0 && <ul className="list-disc pl-5 text-amber-700 dark:text-amber-300">{result.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
            <div className="text-xs text-slate-500">Caddy: {result.apply?.caddy} · Tunnel: {result.apply?.tunnel || '—'}</div>
          </div>
        </Section>
      )}
      <Section title="Proxy / redirection / 404 host trên NPM" actions={<>
        <button className="btn-secondary btn-sm" onClick={() => setSel(new Set(all.filter((x) => !x.already).map((x) => x.key)))}>Chọn tất cả chưa có</button>
        <button className="btn-ghost btn-sm" onClick={() => setSel(new Set())}>Bỏ chọn</button>
      </>}>
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th></th><th>Loại</th><th>Domain</th><th>Đích</th><th>Tuỳ chọn NPM</th></tr></thead>
            <tbody>
              {d.items.map((h: any) => (
                <tr key={h.key} className={h.already ? 'opacity-50' : ''}>
                  <td><input type="checkbox" disabled={h.already} checked={sel.has(h.key)} onChange={(e) => toggle(h.key, e.target.checked)} /></td>
                  <td><Badge color={h.type === 'proxy' ? 'blue' : h.type === 'redirect' ? 'orange' : 'gray'}>{h.type === 'proxy' ? 'proxy' : h.type === 'redirect' ? 'redirect' : '404'}</Badge></td>
                  <td>{h.hosts.join(', ')}</td>
                  <td className="mono">{h.type === 'redirect' ? `${h.redirect_code} → ${h.redirect_to}` : h.type === 'dead' ? '404' : h.upstream}
                    {h.locations?.map((l: any) => <div key={l.path} className="text-slate-500">{l.path} → {l.upstream}</div>)}</td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {h.already && <Badge>đã có</Badge>}
                      {!h.enabled && <Badge>tắt</Badge>}
                      {h.ssl && <Badge color="green">SSL{h.dns_challenge ? ' DNS' : ''}</Badge>}
                      {h.ssl_forced && <Badge color="green">force SSL</Badge>}
                      {h.hsts && <Badge color="green">HSTS</Badge>}
                      {h.websocket && <Badge color="blue">ws</Badge>}
                      {h.block_exploits && <Badge color="gray">block exploits</Badge>}
                      {h.cache_assets && <Badge color="gray">cache</Badge>}
                      {h.access_list_npm_id && <Badge color="blue">access list</Badge>}
                      {h.advanced_config && <Badge color="yellow" title={h.advanced_config}>advanced config</Badge>}
                    </div>
                  </td>
                </tr>
              ))}
              {!d.items.length && <tr><td colSpan={5} className="text-center text-slate-500">Không có host</td></tr>}
            </tbody>
          </table>
        </div>
      </Section>
      {d.streams.length > 0 && (
        <Section title="Streams">
          <table className="table">
            <tbody>
              {d.streams.map((s: any) => (
                <tr key={s.key} className={s.already ? 'opacity-50' : ''}>
                  <td className="w-8"><input type="checkbox" disabled={s.already} checked={sel.has(s.key)} onChange={(e) => toggle(s.key, e.target.checked)} /></td>
                  <td className="font-mono">:{s.listen_port}</td><td><Badge color="blue">{s.protocol}</Badge></td><td className="mono">{s.upstream}</td>
                  <td>{s.already && <Badge>đã có</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Access lists & chứng chỉ">
          <div className="space-y-2 p-4 text-sm">
            {d.access_lists.map((a: any) => <div key={a.name}><b>{a.name}</b> — {a.auth.length} tài khoản, {a.clients.length} rule IP {a.already && <Badge>đã có</Badge>}</div>)}
            {!d.access_lists.length && <div className="text-slate-500">Không có access list</div>}
            <div className="text-xs text-slate-500">{d.certificates.length} chứng chỉ trên NPM — Let's Encrypt sẽ được Caddy cấp lại tự động; chứng chỉ custom cần tải lên lại.</div>
            {d.custom_certs.map((c: any) => <div key={c.npm_id} className="text-xs text-amber-600">Custom: {c.name} ({c.domains.join(', ')})</div>)}
            {d.default_site && <div className="text-xs">Default site NPM: <span className="font-mono">{d.default_site.value}</span></div>}
          </div>
        </Section>
        <Section title="Tuỳ chọn import">
          <div className="space-y-3 p-4 text-sm">
            <label className="flex items-start gap-2"><input type="radio" checked={mode === 'direct'} onChange={() => setMode('direct')} className="mt-1" />
              <span><b>Thay NPM trên 80/443</b> (khuyến nghị) — HTTPS Let's Encrypt, Caddy nhận traffic trực tiếp sau khi chuyển đổi.</span></label>
            <label className="flex items-start gap-2"><input type="radio" checked={mode === 'tunnel'} onChange={() => setMode('tunnel')} className="mt-1" />
              <span><b>Qua Cloudflare Tunnel</b> — domain phải thuộc zone Cloudflare đã cấu hình.</span></label>
            <Toggle checked={acl} onChange={setAcl} label="Import access lists" />
            <Toggle checked={def} onChange={setDef} label="Import default site" />
            <button className="btn-primary" disabled={!sel.size || busy} onClick={run}>{busy ? <Spinner /> : <Download className="h-4 w-4" />}Import {sel.size} mục</button>
          </div>
        </Section>
      </div>
    </div>
  )
}
