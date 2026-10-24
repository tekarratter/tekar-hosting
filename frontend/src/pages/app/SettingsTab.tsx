import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import { ConfirmModal, Field, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'
import type { TabProps } from '../AppDetail'
import { useIsStaff } from '../../auth'

export default function SettingsTab({ app, refetch, isOwner, runJob }: TabProps) {
  const toast = useToast()
  const nav = useNavigate()
  const [name, setName] = useState(app.name)
  const [desc, setDesc] = useState(app.description)
  const [auto, setAuto] = useState(app.auto_update)
  const [owner, setOwner] = useState<number | ''>(app.owner_id ?? '')
  const [busy, setBusy] = useState(false)
  const [del, setDel] = useState(false)
  const [opts, setOpts] = useState({ remove_containers: false, remove_volumes: false, remove_files: false, drop_service_data: false })
  const staff = useIsStaff()
  const [tpl, setTpl] = useState({ name: '', description: '', blank_env: true, shared: false })
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get<any[]>('/api/users'), enabled: isOwner })

  async function save() {
    setBusy(true)
    try {
      const body: any = { name, description: desc, auto_update: auto }
      if (isOwner && owner && owner !== app.owner_id) body.owner_id = owner
      await api.put(`/api/apps/${app.id}`, body)
      toast.success('Đã lưu'); refetch()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  async function remove() {
    try {
      const r = await api.post(`/api/apps/${app.id}/delete`, { ...opts, confirm: app.slug })
      setDel(false)
      if (r.job_id) runJob(Promise.resolve(r), 'Xoá app')
      toast.success('Đang xoá ứng dụng')
      setTimeout(() => nav('/apps'), r.job_id ? 4000 : 0)
    } catch (e) { toast.error(e) }
  }

  return (
    <div className="space-y-4">
      <Section title="Thông tin">
        <div className="space-y-3 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Tên"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            {isOwner && (
              <Field label="Chủ sở hữu">
                <select className="input" value={owner} onChange={(e) => setOwner(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">—</option>
                  {(users.data || []).map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
                </select>
              </Field>
            )}
          </div>
          <Field label="Mô tả"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
          <Toggle checked={auto} onChange={setAuto} label="Tự động cập nhật image hằng ngày (04:30)" />
          <div><button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu</button></div>
        </div>
      </Section>
      <Section title="Lưu làm template">
        <div className="space-y-3 p-4 text-sm">
          <p className="text-slate-600 dark:text-slate-400">Lưu compose, .env, dịch vụ nền tảng và service web của app thành template để tạo app mới nhanh (Tạo app → Template).</p>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Tên template"><input className="input" value={tpl.name} onChange={(e) => setTpl({ ...tpl, name: e.target.value })} placeholder={app.name} /></Field>
            <Field label="Mô tả"><input className="input" value={tpl.description} onChange={(e) => setTpl({ ...tpl, description: e.target.value })} /></Field>
          </div>
          <Toggle checked={tpl.blank_env} onChange={(v) => setTpl({ ...tpl, blank_env: v })} label={<>Bỏ giá trị biến .env (thành <span className="font-mono">KEY=${'{'}KEY{'}'}</span>, người tạo app nhập lại) — nên bật để không lộ mật khẩu</>} />
          {staff && <Toggle checked={tpl.shared} onChange={(v) => setTpl({ ...tpl, shared: v })} label="Chia sẻ cho mọi người dùng" />}
          <button className="btn-secondary" onClick={async () => {
            try { await api.post(`/api/apps/${app.id}/save-template`, { ...tpl, name: tpl.name || app.name }); toast.success('Đã lưu template'); setTpl({ ...tpl, name: '', description: '' }) } catch (e) { toast.error(e) }
          }}>Lưu template</button>
        </div>
      </Section>
      {isOwner && (
        <Section title={<span className="text-rose-600">Vùng nguy hiểm</span>}>
          <div className="flex items-center justify-between p-4 text-sm">
            <div>Gỡ khỏi panel (giữ chạy) hoặc xoá container và (tuỳ chọn) dữ liệu.</div>
            <button className="btn-danger" onClick={() => setDel(true)}>Xoá ứng dụng</button>
          </div>
        </Section>
      )}
      <ConfirmModal open={del} onClose={() => setDel(false)} title={`Xoá ${app.name}`} requireText={app.slug} confirmText="Xoá" onConfirm={remove}>
        <div className="space-y-2">
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" className="mt-1" checked={!opts.remove_containers} onChange={() => setOpts({ ...opts, remove_containers: false, remove_volumes: false, remove_files: false, drop_service_data: false })} />
            <span><b>Gỡ khỏi panel, giữ nguyên container & thư mục</b><div className="text-xs text-slate-500">Tự vận hành tiếp bằng docker compose tại <span className="font-mono">{app.work_dir}</span>.</div></span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" className="mt-1" checked={opts.remove_containers} onChange={() => setOpts({ ...opts, remove_containers: true })} />
            <span><b>Xoá container</b> (docker compose down)</span>
          </label>
          {opts.remove_containers && (
            <div className="space-y-2 pl-6">
              <Toggle checked={opts.remove_volumes} onChange={(v) => setOpts({ ...opts, remove_volumes: v })} label={<span className="text-rose-600">Xoá volume (mất dữ liệu)</span>} />
              <Toggle checked={opts.remove_files} onChange={(v) => setOpts({ ...opts, remove_files: v })} label={<span className="text-rose-600">Xoá thư mục compose/.env</span>} />
              <Toggle checked={opts.drop_service_data} onChange={(v) => setOpts({ ...opts, drop_service_data: v })} label={<span className="text-rose-600">Xoá database/bucket dùng chung</span>} />
            </div>
          )}
        </div>
      </ConfirmModal>
    </div>
  )
}
