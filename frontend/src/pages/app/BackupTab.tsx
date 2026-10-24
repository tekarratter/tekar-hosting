import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Archive } from 'lucide-react'
import { api, type BackupTarget, type PlatformService } from '../../api'
import { Field, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'
import { BackupTable } from '../Backups'
import type { TabProps } from '../AppDetail'

const PRESETS = [['', 'Không tự động'], ['0 2 * * *', 'Hằng ngày 02:00'], ['0 */6 * * *', 'Mỗi 6 giờ'], ['0 3 * * 0', 'Chủ nhật 03:00']]

export default function BackupTab({ app, refetch, runJob, canDeploy, isOwner }: TabProps) {
  const toast = useToast()
  const svcs = useQuery({ queryKey: ['platform-services'], queryFn: () => api.get<PlatformService[]>('/api/platform/services'), enabled: app.backup_target_id != null })
  const targets = useQuery({ queryKey: ['backup-targets'], queryFn: () => api.get<BackupTarget[]>('/api/storage/targets') })
  const list = useQuery({ queryKey: ['backups', app.id], queryFn: () => api.get<any[]>(`/api/backups?app_id=${app.id}`), refetchInterval: 15000 })
  const [cron, setCron] = useState(app.backup_cron || '')
  const [ret, setRet] = useState(app.backup_retention)
  const [target, setTarget] = useState<number | ''>(app.backup_target_id ?? '')
  const [dest, setDest] = useState<number | ''>(app.backup_destination_id ?? '')
  const [keepLocal, setKeepLocal] = useState(app.backup_keep_local ?? true)
  const [stop, setStop] = useState(app.backup_stop_app)
  const [binds, setBinds] = useState(app.backup_include_binds)
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      await api.put(`/api/apps/${app.id}`, { backup_cron: cron || null, backup_retention: ret, backup_target_id: target || null, backup_destination_id: dest || null, backup_keep_local: keepLocal, backup_stop_app: stop, backup_include_binds: binds })
      toast.success('Đã lưu chính sách backup'); refetch()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <Section title="Chính sách backup">
        <div className="space-y-3 p-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Lịch (cron)" hint="phút giờ ngày tháng thứ">
              <div className="flex gap-1">
                <input className="input font-mono" value={cron} disabled={!canDeploy} onChange={(e) => setCron(e.target.value)} placeholder="0 2 * * *" />
                <select className="input w-36" disabled={!canDeploy} value="" onChange={(e) => setCron(e.target.value)}>
                  <option value="" disabled>Mẫu…</option>
                  {PRESETS.map(([v, l]) => <option key={l} value={v}>{l}</option>)}
                </select>
              </div>
            </Field>
            <Field label="Giữ lại (số bản)"><input className="input" type="number" min={1} disabled={!canDeploy} value={ret} onChange={(e) => setRet(Number(e.target.value))} /></Field>
            <Field label="Đích backup ngoài máy" hint={<>S3 / SFTP / thư mục — cấu hình ở <a href="/storage" className="underline">Lưu trữ & Backup</a></>}>
              <select className="input" disabled={!canDeploy} value={dest} onChange={(e) => setDest(e.target.value ? Number(e.target.value) : '')}>
                <option value="">Chỉ lưu cục bộ</option>
                {(targets.data || []).map((t) => <option key={t.id} value={t.id}>{t.name} ({t.kind})</option>)}
              </select>
            </Field>
            {app.backup_target_id != null && (
              <Field label="S3 dịch vụ nền tảng (cũ)" hint="Cách cũ — nên chuyển sang Đích backup">
                <select className="input" disabled={!canDeploy} value={target} onChange={(e) => setTarget(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">Không dùng</option>
                  {(svcs.data || []).filter((s) => s.kind === 's3').map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>
            )}
          </div>
          <div className="flex flex-wrap gap-4">
            <Toggle checked={stop} onChange={(v) => canDeploy && setStop(v)} label="Dừng app khi backup (nhất quán hơn)" />
            <Toggle checked={binds} onChange={(v) => canDeploy && setBinds(v)} label="Gồm thư mục bind mount" />
            {dest !== '' && <Toggle checked={keepLocal} onChange={(v) => canDeploy && setKeepLocal(v)} label="Giữ cả bản cục bộ" />}
          </div>
          <div className="text-xs text-slate-500">Mỗi bản gồm: volume, bind mount, dump logic DB trong app (postgres/mysql), database/key/bucket dùng chung đã chọn backup, compose + .env.</div>
          {canDeploy && (
            <div className="flex gap-2">
              <button className="btn-secondary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu</button>
              <button className="btn-primary" onClick={() => runJob(api.post(`/api/backups/apps/${app.id}`), 'Backup')}><Archive className="h-4 w-4" />Backup ngay</button>
            </div>
          )}
        </div>
      </Section>
      <Section title="Các bản backup">
        <BackupTable backups={list.data || []} loading={list.isLoading} onChanged={() => list.refetch()} runJob={runJob} canRestore={isOwner} slugs={{ [app.id]: app.slug }} hideApp />
      </Section>
    </div>
  )
}
