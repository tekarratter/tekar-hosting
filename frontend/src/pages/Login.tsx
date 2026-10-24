import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Server } from 'lucide-react'
import { api } from '../api'
import { Spinner } from '../components/ui'

export default function Login() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [needOtp, setNeedOtp] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      const r = await api.post('/api/auth/login', { email, password, otp: needOtp ? otp : undefined })
      if (r.otp_required) { setNeedOtp(true); return }
      qc.setQueryData(['me'], r.user)
      nav('/', { replace: true })
    } catch (e: any) {
      setErr(e.message)
    } finally { setBusy(false) }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white"><Server className="h-5 w-5" /></div>
          <div>
            <div className="font-bold">Tekar Hosting</div>
            <div className="text-xs text-slate-500">Đăng nhập để quản lý hệ thống</div>
          </div>
        </div>
        {!needOtp ? (
          <>
            <div>
              <label className="label">Tên đăng nhập / Email</label>
              <input className="input" type="text" autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div>
              <label className="label">Mật khẩu</label>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
          </>
        ) : (
          <div>
            <label className="label">Mã xác thực 2 lớp (6 số)</label>
            <input className="input text-center font-mono text-lg tracking-widest" inputMode="numeric" autoFocus maxLength={6} value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))} />
          </div>
        )}
        {err && <div className="text-sm text-rose-600">{err}</div>}
        <button className="btn-primary w-full justify-center" disabled={busy}>{busy && <Spinner />}{needOtp ? 'Xác nhận' : 'Đăng nhập'}</button>
        {needOtp && <button type="button" className="btn-ghost w-full justify-center" onClick={() => { setNeedOtp(false); setOtp('') }}>Quay lại</button>}
      </form>
    </div>
  )
}
