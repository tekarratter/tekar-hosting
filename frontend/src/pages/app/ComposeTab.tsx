import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Section, Spinner } from '../../components/ui'
import { useToast } from '../../components/toast'
import type { TabProps } from '../AppDetail'

export default function ComposeTab({ app, refetch, runJob }: TabProps) {
  const toast = useToast()
  const [compose, setCompose] = useState(app.compose_yaml || '')
  const [env, setEnv] = useState(app.env || '')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setCompose(app.compose_yaml || ''); setEnv(app.env || '') }, [app.id]) // eslint-disable-line

  if (app.kind !== 'managed') {
    return (
      <div className="space-y-4">
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          App adopted: compose nằm trong thư mục dự án gốc, sửa trực tiếp tại <span className="font-mono">{app.work_dir}</span> rồi bấm Deploy.
          Biến môi trường của dịch vụ nền tảng (tab Dịch vụ) cần thêm thủ công vào .env của dự án.
        </div>
        {Object.entries(app.compose_sources || {}).map(([f, txt]) => (
          <Section key={f} title={<span className="font-mono">{f}</span>}>
            <pre className="max-h-[600px] overflow-auto bg-slate-950 p-3 font-mono text-xs text-slate-200">{txt}</pre>
          </Section>
        ))}
      </div>
    )
  }

  async function save(deploy: boolean) {
    setBusy(true)
    try {
      await api.put(`/api/apps/${app.id}`, { compose_yaml: compose, env })
      toast.success('Đã lưu')
      refetch()
      if (deploy) runJob(api.post(`/api/apps/${app.id}/deploy`), 'Deploy')
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  return (
    <div className="space-y-4">
      <Section title="docker-compose.yml">
        <textarea className="block h-[460px] w-full resize-y bg-slate-950 p-3 font-mono text-xs text-slate-100 outline-none" spellCheck={false} value={compose} onChange={(e) => setCompose(e.target.value)} />
      </Section>
      <Section title=".env (mã hoá khi lưu)">
        <textarea className="block h-48 w-full resize-y bg-slate-950 p-3 font-mono text-xs text-slate-100 outline-none" spellCheck={false} value={env} onChange={(e) => setEnv(e.target.value)} />
      </Section>
      <div className="text-xs text-slate-500">Biến của dịch vụ nền tảng được tự động thêm vào cuối .env khi deploy. Panel sinh thêm compose.th.yml (label, giới hạn tài nguyên, mạng edge).</div>
      <div className="flex gap-2">
        <button className="btn-secondary" disabled={busy} onClick={() => save(false)}>{busy && <Spinner />}Lưu</button>
        <button className="btn-primary" disabled={busy} onClick={() => save(true)}>Lưu & Deploy</button>
      </div>
    </div>
  )
}
