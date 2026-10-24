import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, ShieldOff, Boxes } from 'lucide-react'
import { api, type User } from '../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, Modal, PageHeader, Spinner, Toggle } from '../components/ui'
import { useToast } from '../components/toast'
import { useAuth } from '../auth'
import { fmtAgo } from '../util'

const ROLE_DESC: Record<string, string> = { admin: 'Toàn quyền, quản lý user & cài đặt', operator: 'Quản lý mọi app & hạ tầng', member: 'Chỉ app được cấp quyền' }

function UserForm({ user, onClose, onSaved }: { user: User | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [f, setF] = useState({ email: user?.email || '', name: user?.name || '', role: user?.role || 'member', is_active: user?.is_active ?? true, password: '' })
  const [busy, setBusy] = useState(false)
  async function save() {
    setBusy(true)
    try {
      const body = { ...f, password: f.password || null }
      if (user) await api.put(`/api/users/${user.id}`, body)
      else await api.post('/api/users', body)
      toast.success('Đã lưu'); onSaved(); onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  return (
    <Modal open onClose={onClose} title={user ? `Sửa ${user.email}` : 'Thêm người dùng'} size="sm" footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu</button>
    </>}>
      <div className="space-y-3">
        <Field label="Tên đăng nhập / Email"><input className="input" disabled={!!user} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Tên"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Vai trò" hint={ROLE_DESC[f.role]}>
          <select className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as any })}>{Object.keys(ROLE_DESC).map((r) => <option key={r}>{r}</option>)}</select>
        </Field>
        <Field label={user ? 'Mật khẩu mới (để trống = giữ nguyên)' : 'Mật khẩu (≥ 8 ký tự)'}><input className="input" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <Toggle checked={f.is_active} onChange={(v) => setF({ ...f, is_active: v })} label="Đang hoạt động" />
      </div>
    </Modal>
  )
}

function UserApps({ user, onClose }: { user: User; onClose: () => void }) {
  const q = useQuery({ queryKey: ['user-apps', user.id], queryFn: () => api.get(`/api/users/${user.id}/apps`) })
  return (
    <Modal open onClose={onClose} title={`Ứng dụng của ${user.email}`} size="sm">
      {q.isLoading ? <Loading /> : (
        <div className="space-y-2 text-sm">
          {q.data.owned.map((a: any) => <div key={a.id}>{a.name} <Badge color="purple">owner</Badge></div>)}
          {q.data.member.map((a: any) => <div key={a.id}>{a.name} <Badge>{a.role}</Badge></div>)}
          {!q.data.owned.length && !q.data.member.length && <div className="text-slate-500">Chưa có (cấp quyền ở tab Thành viên của app)</div>}
        </div>
      )}
    </Modal>
  )
}

export default function UsersPage() {
  const toast = useToast()
  const { user: me } = useAuth()
  const q = useQuery({ queryKey: ['users'], queryFn: () => api.get<User[]>('/api/users') })
  const [edit, setEdit] = useState<User | null | undefined>(undefined)
  const [apps, setApps] = useState<User | null>(null)
  const [del, setDel] = useState<User | null>(null)
  if (me?.role !== 'admin') return <ErrorBox error="Cần quyền admin" />
  return (
    <div>
      <PageHeader title="Người dùng" subtitle="Phân quyền: admin / operator / member + quyền theo từng app" actions={<button className="btn-primary" onClick={() => setEdit(null)}><Plus className="h-4 w-4" />Thêm</button>} />
      <ErrorBox error={q.error} />
      <div className="card overflow-x-auto">
        {q.isLoading ? <Loading /> : (
          <table className="table">
            <thead><tr><th>Người dùng</th><th>Vai trò</th><th>2FA</th><th>Đăng nhập</th><th></th></tr></thead>
            <tbody>
              {(q.data || []).map((u) => (
                <tr key={u.id} className={u.is_active ? '' : 'opacity-50'}>
                  <td><div className="font-medium">{u.name || '—'}</div><div className="text-xs text-slate-500">{u.email}</div></td>
                  <td><Badge color={u.role === 'admin' ? 'red' : u.role === 'operator' ? 'blue' : 'gray'}>{u.role}</Badge>{!u.is_active && <Badge>khoá</Badge>}</td>
                  <td>{u.totp ? <Badge color="green">bật</Badge> : '—'}</td>
                  <td className="text-xs">{fmtAgo(u.last_login_at)}</td>
                  <td className="whitespace-nowrap text-right">
                    <button className="btn-ghost btn-sm" title="Ứng dụng" onClick={() => setApps(u)}><Boxes className="h-3.5 w-3.5" /></button>
                    <button className="btn-ghost btn-sm" title="Sửa" onClick={() => setEdit(u)}><Pencil className="h-3.5 w-3.5" /></button>
                    {u.totp && <button className="btn-ghost btn-sm" title="Reset 2FA" onClick={async () => { try { await api.post(`/api/users/${u.id}/reset-2fa`); toast.success('Đã tắt 2FA'); q.refetch() } catch (e) { toast.error(e) } }}><ShieldOff className="h-3.5 w-3.5" /></button>}
                    {u.id !== me.id && <button className="btn-ghost btn-sm" title="Xoá" onClick={() => setDel(u)}><Trash2 className="h-3.5 w-3.5" /></button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {edit !== undefined && <UserForm user={edit} onClose={() => setEdit(undefined)} onSaved={() => q.refetch()} />}
      {apps && <UserApps user={apps} onClose={() => setApps(null)} />}
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá người dùng" requireText={del?.email}
        onConfirm={async () => { try { await api.del(`/api/users/${del!.id}`); toast.success('Đã xoá'); setDel(null); q.refetch() } catch (e) { toast.error(e) } }} />
    </div>
  )
}
