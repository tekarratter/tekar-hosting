import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { QRCodeSVG } from 'qrcode.react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../api'
import { Badge, Code, Field, Modal, PageHeader, Section, Spinner } from '../components/ui'
import { useToast } from '../components/toast'
import { useAuth } from '../auth'
import { GitConnections } from '../components/Git'
import { fmtAgo, fmtDate } from '../util'

export default function Account() {
  const toast = useToast()
  const { user, refresh } = useAuth()
  const [cur, setCur] = useState('')
  const [nw, setNw] = useState('')
  const [busy, setBusy] = useState(false)
  const [totp, setTotp] = useState<any>(null)
  const [otp, setOtp] = useState('')
  const [disablePw, setDisablePw] = useState('')
  const [disableOtp, setDisableOtp] = useState('')
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api.get<any[]>('/api/auth/tokens') })
  const [tname, setTname] = useState('')
  const [newToken, setNewToken] = useState<string | null>(null)

  return (
    <div className="space-y-4">
      <PageHeader title="Tài khoản" subtitle={`${user?.email} · ${user?.role}`} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Đổi mật khẩu">
          <div className="space-y-3 p-4">
            <Field label="Mật khẩu hiện tại"><input className="input" type="password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
            <Field label="Mật khẩu mới (≥ 8 ký tự)"><input className="input" type="password" value={nw} onChange={(e) => setNw(e.target.value)} /></Field>
            <button className="btn-primary" disabled={busy || !cur || nw.length < 8} onClick={async () => {
              setBusy(true)
              try { await api.post('/api/auth/password', { current: cur, new: nw }); toast.success('Đã đổi mật khẩu'); setCur(''); setNw('') } catch (e) { toast.error(e) } finally { setBusy(false) }
            }}>{busy && <Spinner />}Đổi mật khẩu</button>
          </div>
        </Section>
        <Section title={<span>Xác thực 2 lớp (TOTP) {user?.totp && <Badge color="green">đang bật</Badge>}</span>}>
          <div className="space-y-3 p-4 text-sm">
            {user?.totp ? (
              <div className="flex gap-2">
                <input className="input" type="password" placeholder="Mật khẩu" value={disablePw} onChange={(e) => setDisablePw(e.target.value)} />
                <input className="input w-32 font-mono" inputMode="numeric" placeholder="Mã OTP" value={disableOtp} onChange={(e) => setDisableOtp(e.target.value)} />
                <button className="btn-danger" disabled={!disablePw || !disableOtp} onClick={async () => { try { await api.post('/api/auth/totp/disable', { current: disablePw, otp: disableOtp }); toast.success('Đã tắt 2FA'); setDisablePw(''); setDisableOtp(''); refresh() } catch (e) { toast.error(e) } }}>Tắt</button>
              </div>
            ) : totp ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-start gap-4">
                  <div className="rounded bg-white p-2"><QRCodeSVG value={totp.uri} size={160} /></div>
                  <div className="min-w-0 flex-1 space-y-2">
                    <div>Quét bằng Google Authenticator / Authy, hoặc nhập khoá:</div>
                    <Code>{totp.secret}</Code>
                    <div className="break-all font-mono text-[11px] text-slate-500">{totp.uri}</div>
                  </div>
                </div>
                <div className="flex gap-2">
                  <input className="input w-40 text-center font-mono tracking-widest" maxLength={6} placeholder="123456" value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))} />
                  <button className="btn-primary" disabled={otp.length !== 6} onClick={async () => { try { await api.post('/api/auth/totp/enable', { token: totp.token, otp }); toast.success('Đã bật 2FA'); setTotp(null); setOtp(''); refresh() } catch (e) { toast.error(e) } }}>Xác nhận</button>
                </div>
              </div>
            ) : (
              <button className="btn-secondary" onClick={async () => { try { setTotp(await api.post('/api/auth/totp/setup')) } catch (e) { toast.error(e) } }}>Thiết lập 2FA</button>
            )}
          </div>
        </Section>
      </div>
      {user?.role !== 'member' && <Section title="Kết nối Git (GitHub / GitLab)"><GitConnections /></Section>}
      <Section title="API token (MCP / CLI / CI)">
        <div className="space-y-3 p-4">
          <div className="flex gap-2">
            <input className="input w-72" placeholder="Tên token, vd claude-code-laptop" value={tname} onChange={(e) => setTname(e.target.value)} />
            <button className="btn-primary" disabled={!tname} onClick={async () => { try { const r = await api.post('/api/auth/tokens', { name: tname }); setNewToken(r.token); setTname(''); tokens.refetch() } catch (e) { toast.error(e) } }}><Plus className="h-4 w-4" />Tạo</button>
          </div>
          <table className="table">
            <thead><tr><th>Tên</th><th>Tiền tố</th><th>Tạo</th><th>Dùng lần cuối</th><th></th></tr></thead>
            <tbody>
              {(tokens.data || []).map((t) => (
                <tr key={t.id}><td>{t.name}</td><td className="mono">{t.prefix}…</td><td className="text-xs">{fmtDate(t.created_at)}</td><td className="text-xs">{fmtAgo(t.last_used_at)}</td>
                  <td className="text-right"><button className="btn-ghost btn-sm" onClick={async () => { await api.del(`/api/auth/tokens/${t.id}`); tokens.refetch() }}><Trash2 className="h-3.5 w-3.5" /></button></td></tr>
              ))}
              {!tokens.data?.length && <tr><td colSpan={5} className="text-center text-slate-500">Chưa có token</td></tr>}
            </tbody>
          </table>
        </div>
      </Section>
      <Modal open={!!newToken} onClose={() => setNewToken(null)} title="Token mới" footer={<button className="btn-primary" onClick={() => setNewToken(null)}>Đã lưu</button>}>
        <div className="space-y-3 text-sm">
          <div className="text-amber-600">Token chỉ hiển thị một lần — hãy lưu lại.</div>
          {newToken && <Code>{newToken}</Code>}
          <div>Dùng với Claude Code:</div>
          {newToken && <Code>{`claude mcp add --transport http tekar-hosting ${location.origin}/mcp --header "Authorization: Bearer ${newToken}"`}</Code>}
        </div>
      </Modal>
    </div>
  )
}
