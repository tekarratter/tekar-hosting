import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowRightLeft, CheckCircle2, Undo2 } from 'lucide-react'
import { api } from '../../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, Section, Spinner } from '../../components/ui'
import { useToast } from '../../components/toast'
import { useAuth } from '../../auth'

interface Settings {
  default_site: { mode: string; value: string }; ports: { http: number; https: number }; public: boolean; acme_email: string
  published: string[]; conflicts: string[]; npm: { name: string; state: string; image: string }[]; npm_stopped: string[]
}

export default function ProxySettings({ onJob }: { onJob: (id: number, title: string) => void }) {
  const toast = useToast()
  const { user } = useAuth()
  const admin = user?.role === 'admin'
  const q = useQuery({ queryKey: ['proxy-settings'], queryFn: () => api.get<Settings>('/api/proxy/settings') })
  const check = useQuery({ queryKey: ['cutover-check'], queryFn: () => api.get('/api/proxy/cutover/check'), enabled: admin })
  const [mode, setMode] = useState('404')
  const [value, setValue] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState('')
  const [cut, setCut] = useState(false)
  const [rb, setRb] = useState(false)
  const [httpPort, setHttpPort] = useState(80)
  const [httpsPort, setHttpsPort] = useState(443)
  useEffect(() => {
    if (q.data) { setMode(q.data.default_site?.mode || '404'); setValue(q.data.default_site?.value || ''); setEmail(q.data.acme_email || '') }
  }, [q.data])

  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  const s = q.data!

  async function saveDefault() {
    setBusy('default')
    try { await api.put('/api/proxy/default-site', { mode, value }); toast.success('Đã lưu default site') } catch (e) { toast.error(e) } finally { setBusy('') }
  }
  async function saveAcme() {
    setBusy('acme')
    try { await api.put('/api/proxy/acme', { acme_email: email }); toast.success('Đã lưu email ACME') } catch (e) { toast.error(e) } finally { setBusy('') }
  }

  return (
    <div className="space-y-4">
      <Section title="Cổng & trạng thái Caddy">
        <div className="grid gap-3 p-4 text-sm md:grid-cols-2">
          <div className="space-y-1">
            <div>HTTP: <b className="font-mono">{s.ports.http}</b> · HTTPS: <b className="font-mono">{s.ports.https}</b></div>
            <div>{s.public ? <Badge color="green">Caddy đang giữ cổng public (đã thay NPM) — ACME HTTP-01 bật</Badge> : <Badge color="yellow">Chạy song song NPM (cổng phụ) — traffic public qua Cloudflare Tunnel</Badge>}</div>
            <div className="text-xs text-slate-500">Cổng publish: <span className="font-mono">{s.published.join(', ')}</span></div>
            {s.conflicts.length > 0 && <div className="text-xs text-rose-600">Xung đột: {s.conflicts.join('; ')}</div>}
          </div>
          <div className="space-y-1">
            <div className="font-medium">Nginx Proxy Manager</div>
            {s.npm.map((n) => <div key={n.name} className="text-xs"><span className="font-mono">{n.name}</span> <Badge color={n.state === 'running' ? 'green' : 'gray'}>{n.state}</Badge></div>)}
            {!s.npm.length && <div className="text-xs text-slate-500">Không phát hiện container NPM</div>}
          </div>
        </div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Default site (domain chưa cấu hình)">
          <div className="space-y-3 p-4">
            <Field label="Khi truy cập domain không có host">
              <select className="input" value={mode} disabled={!admin} onChange={(e) => setMode(e.target.value)}>
                <option value="404">Trả 404</option>
                <option value="congrats">Trang chào mừng</option>
                <option value="redirect">Chuyển hướng</option>
                <option value="html">HTML tuỳ chỉnh</option>
                <option value="abort">Đóng kết nối (như nginx 444)</option>
              </select>
            </Field>
            {mode === 'redirect' && <Field label="URL chuyển tới"><input className="input" value={value} disabled={!admin} onChange={(e) => setValue(e.target.value)} placeholder="https://example.com" /></Field>}
            {mode === 'html' && <Field label="HTML"><textarea className="input font-mono" rows={5} value={value} disabled={!admin} onChange={(e) => setValue(e.target.value)} /></Field>}
            {admin && <button className="btn-primary" disabled={busy === 'default'} onClick={saveDefault}>{busy === 'default' && <Spinner />}Lưu</button>}
          </div>
        </Section>
        <Section title="Let's Encrypt">
          <div className="space-y-3 p-4">
            <Field label="Email ACME" hint="Nhận thông báo hết hạn từ Let's Encrypt / ZeroSSL">
              <input className="input" type="email" value={email} disabled={!admin} onChange={(e) => setEmail(e.target.value)} placeholder="admin@example.com" />
            </Field>
            {admin && <button className="btn-primary" disabled={busy === 'acme'} onClick={saveAcme}>{busy === 'acme' && <Spinner />}Lưu</button>}
          </div>
        </Section>
      </div>

      {admin && (
        <Section title={<span className="flex items-center gap-1"><ArrowRightLeft className="h-4 w-4" />Chuyển đổi từ Nginx Proxy Manager sang Caddy</span>}>
          <div className="space-y-3 p-4 text-sm">
            <ol className="list-decimal space-y-1 pl-5 text-slate-600 dark:text-slate-300">
              <li>Import các host từ NPM (tab "Import NPM", chế độ "Thay NPM trên 80/443") và kiểm tra cấu hình.</li>
              <li>Bấm chuyển đổi: panel dừng container NPM, tạo lại th-caddy giữ cổng {httpPort}/{httpsPort}.</li>
              <li>Caddy cấp chứng chỉ Let's Encrypt (HTTP-01) cho các domain trong vài phút.</li>
              <li>Lỗi giữa chừng → tự hoàn tác (Caddy về cổng phụ, khởi động lại NPM). Có thể hoàn tác thủ công bất cứ lúc nào.</li>
            </ol>
            {check.isLoading && <Loading text="Đang kiểm tra…" />}
            {check.data && (
              <div className="space-y-1">
                {check.data.blocking.map((b: string, i: number) => <div key={i} className="flex items-start gap-1 text-rose-600"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{b}</div>)}
                {check.data.warnings.map((w: string, i: number) => <div key={i} className="flex items-start gap-1 text-amber-600"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{w}</div>)}
                {!check.data.blocking.length && !check.data.warnings.length && <div className="flex items-center gap-1 text-emerald-600"><CheckCircle2 className="h-4 w-4" />Sẵn sàng chuyển đổi</div>}
                <div className="text-xs text-slate-500">NPM sẽ dừng: {check.data.npm_containers.join(', ') || '—'} · {check.data.direct_routes} host truy cập trực tiếp</div>
              </div>
            )}
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Cổng HTTP"><input className="input w-24" type="number" value={httpPort} onChange={(e) => setHttpPort(Number(e.target.value))} /></Field>
              <Field label="Cổng HTTPS"><input className="input w-24" type="number" value={httpsPort} onChange={(e) => setHttpsPort(Number(e.target.value))} /></Field>
              <button className="btn-danger" disabled={!!check.data?.blocking?.length} onClick={() => setCut(true)}><ArrowRightLeft className="h-4 w-4" />Chuyển đổi ngay</button>
              {(s.public || s.npm_stopped.length > 0) && <button className="btn-secondary" onClick={() => setRb(true)}><Undo2 className="h-4 w-4" />Hoàn tác về NPM</button>}
            </div>
          </div>
        </Section>
      )}
      <ConfirmModal open={cut} onClose={() => setCut(false)} title="Chuyển 80/443 sang Caddy" requireText="CHUYEN" confirmText="Chuyển đổi"
        message={<>NPM sẽ bị dừng và Caddy nhận cổng {httpPort}/{httpsPort}. Các site đang chạy qua NPM sẽ gián đoạn vài giây.</>}
        onConfirm={async () => {
          setCut(false)
          try { const r = await api.post('/api/proxy/cutover', { confirm: 'CHUYEN', http_port: httpPort, https_port: httpsPort }); onJob(r.job_id, 'Chuyển đổi NPM → Caddy') } catch (e) { toast.error(e) }
        }} />
      <ConfirmModal open={rb} onClose={() => setRb(false)} title="Hoàn tác về NPM" confirmText="Hoàn tác"
        message="Caddy về cổng phụ, các container NPM đã dừng sẽ được khởi động lại."
        onConfirm={async () => {
          setRb(false)
          try { const r = await api.post('/api/proxy/rollback'); onJob(r.job_id, 'Hoàn tác về NPM') } catch (e) { toast.error(e) }
        }} />
    </div>
  )
}
