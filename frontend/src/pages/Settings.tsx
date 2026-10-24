import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Save, Send, Trash } from 'lucide-react'
import { api } from '../api'
import { ErrorBox, Field, Loading, PageHeader, Section, Spinner, Toggle } from '../components/ui'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { fmtBytes } from '../util'

const MODELS = ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1']

export default function SettingsPage() {
  const toast = useToast()
  const q = useQuery({ queryKey: ['settings'], queryFn: () => api.get('/api/settings') })
  const df = useQuery({ queryKey: ['system-df'], queryFn: () => api.get('/api/system/df') })
  const [f, setF] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)
  const [prune, setPrune] = useState({ all_images: false, volumes: false })
  const [job, setJob] = useState<number | null>(null)
  const [jobTitle, setJobTitle] = useState('Dọn dẹp Docker')
  const pb = useQuery({ queryKey: ['panel-backups'], queryFn: () => api.get('/api/system/panel-backups') })
  useEffect(() => { if (q.data) setF(q.data) }, [q.data])

  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  const set = (k: string, v: any) => setF({ ...f, [k]: v })

  async function save() {
    setBusy(true)
    try {
      const body: Record<string, any> = {}
      Object.keys(f).forEach((k) => { if (k !== '_info' && JSON.stringify(f[k]) !== JSON.stringify(q.data[k])) body[k] = f[k] })
      ;['alert_cpu_pct', 'alert_mem_pct', 'alert_disk_pct', 'disk_tg_pct', 'disk_tg_image_days', 'panel_backup_keep'].forEach((k) => { if (k in body) body[k] = Number(body[k]) })
      const r = await api.put('/api/settings', body)
      toast.success(`Đã lưu ${r.changed.length} mục`); q.refetch()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  const secret = (k: string, label: string, hint?: string) => (
    <Field label={label} hint={hint}>
      <input className="input font-mono" type="password" autoComplete="new-password" placeholder={q.data[k] || 'chưa đặt'} value={f[k]?.startsWith?.('••••') ? '' : f[k] || ''}
        onChange={(e) => set(k, e.target.value || q.data[k])} />
    </Field>
  )

  return (
    <div className="space-y-4">
      <PageHeader title="Cài đặt hệ thống" actions={<button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu thay đổi</button>} />

      <Section title="Claude (trợ lý & báo cáo)">
        <div className="grid gap-3 p-4 md:grid-cols-3">
          {secret('anthropic_api_key_secret', 'Anthropic API key', 'Lấy tại console.anthropic.com')}
          <Field label="Model">
            <select className="input" value={f.claude_model || ''} onChange={(e) => set('claude_model', e.target.value)}>{MODELS.map((m) => <option key={m}>{m}</option>)}</select>
          </Field>
          <Field label="Effort" hint="Cao hơn = kỹ hơn nhưng chậm/tốn hơn">
            <select className="input" value={f.claude_effort || 'medium'} onChange={(e) => set('claude_effort', e.target.value)}>{['low', 'medium', 'high', 'xhigh', 'max'].map((m) => <option key={m}>{m}</option>)}</select>
          </Field>
          <Field label="Lịch báo cáo tự động (cron)" hint="Gửi qua Telegram/webhook; để trống = tắt"><input className="input font-mono" value={f.report_cron || ''} onChange={(e) => set('report_cron', e.target.value)} placeholder="0 8 * * *" /></Field>
        </div>
      </Section>

      <Section title="Thông báo" actions={<button className="btn-secondary btn-sm" onClick={async () => { try { await api.post('/api/settings/test-notify'); toast.success('Đã gửi thử (lưu trước nếu vừa đổi)') } catch (e) { toast.error(e) } }}><Send className="h-3.5 w-3.5" />Gửi thử</button>}>
        <div className="grid gap-3 p-4 md:grid-cols-3">
          {secret('telegram_bot_token_secret', 'Telegram bot token')}
          <Field label="Telegram chat ID"><input className="input font-mono" value={f.telegram_chat_id || ''} onChange={(e) => set('telegram_chat_id', e.target.value)} /></Field>
          <Field label="Webhook URL" hint="POST JSON {level,title,message}"><input className="input font-mono" value={f.webhook_url || ''} onChange={(e) => set('webhook_url', e.target.value)} /></Field>
        </div>
      </Section>

      <Section title="Cảnh báo ổ đĩa qua Telegram" actions={<button className="btn-secondary btn-sm" disabled={!q.data.disk_tg_enabled} title={q.data.disk_tg_enabled ? '' : 'Bật và lưu trước'} onClick={async () => { try { await api.post('/api/settings/test-disk-alert'); toast.success('Đã gửi tin thử kèm nút') } catch (e) { toast.error(e) } }}><Send className="h-3.5 w-3.5" />Gửi thử</button>}>
        <div className="space-y-3 p-4 text-sm">
          <Toggle checked={!!f.disk_tg_enabled} onChange={(v) => set('disk_tg_enabled', v)} label="Gửi Telegram khi ổ đĩa vượt ngưỡng, kèm nút xoá image cũ" />
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Ngưỡng ổ đĩa (%)"><input className="input" type="number" value={f.disk_tg_pct ?? ''} onChange={(e) => set('disk_tg_pct', e.target.value)} /></Field>
            <Field label="Xoá image cũ hơn (ngày)" hint="Chỉ image không container nào dùng (kể cả container đang dừng)"><input className="input" type="number" value={f.disk_tg_image_days ?? ''} onChange={(e) => set('disk_tg_image_days', e.target.value)} /></Field>
          </div>
          <p className="text-xs text-slate-500">
            Dùng bot token + chat ID ở mục Thông báo. Gửi một lần khi vượt ngưỡng, gửi lại sau khi ổ đĩa xuống dưới ngưỡng − 5%. Khi bật, mục này thay cho cảnh báo "Ổ đĩa (%)" bên dưới.
            {' '}Nút chỉ dùng được một lần và chỉ trong đúng chat đã cấu hình. Panel nhận lượt bấm bằng <span className="font-mono">getUpdates</span>, nên bot phải dành riêng cho panel (không đặt webhook, không app nào khác dùng chung).
          </p>
        </div>
      </Section>

      <Section title="Ngưỡng cảnh báo & lịch quét">
        <div className="grid gap-3 p-4 md:grid-cols-4">
          <Field label="CPU host (%) kéo dài 5 phút"><input className="input" type="number" value={f.alert_cpu_pct ?? ''} onChange={(e) => set('alert_cpu_pct', e.target.value)} /></Field>
          <Field label="RAM host (%)"><input className="input" type="number" value={f.alert_mem_pct ?? ''} onChange={(e) => set('alert_mem_pct', e.target.value)} /></Field>
          <Field label="Ổ đĩa (%)"><input className="input" type="number" value={f.alert_disk_pct ?? ''} onChange={(e) => set('alert_disk_pct', e.target.value)} /></Field>
          <Field label="Lịch quét bảo mật (cron)"><input className="input font-mono" value={f.scan_cron || ''} onChange={(e) => set('scan_cron', e.target.value)} /></Field>
        </div>
      </Section>

      <Section title="Môi trường phát triển (SSH)">
        <div className="grid gap-3 p-4 md:grid-cols-2">
          <Field label="Host SSH hiển thị cho người dùng" hint="IP/tên miền trỏ thẳng về máy chủ (không qua Cloudflare proxy), vd 192.168.50.241"><input className="input font-mono" value={f.dev_ssh_host || ''} onChange={(e) => set('dev_ssh_host', e.target.value)} placeholder="ssh.example.com" /></Field>
          <Field label="Dải cổng SSH" hint="Mỗi app phát triển nhận một cổng trống trong dải này"><input className="input font-mono" value={f.dev_ssh_ports || ''} onChange={(e) => set('dev_ssh_ports', e.target.value)} placeholder="2201-2299" /></Field>
        </div>
      </Section>

      <Section title="Khác">
        <div className="grid gap-3 p-4 md:grid-cols-2">
          <Field label="URL công khai của panel" hint="Dùng trong link thông báo / hướng dẫn MCP"><input className="input font-mono" value={f.public_panel_url || ''} onChange={(e) => set('public_panel_url', e.target.value)} placeholder="https://panel.example.com" /></Field>
          {secret('registry_auths_secret', 'Registry credentials (JSON)', '[{"registry":"registry.example.com","username":"…","password":"…"}]')}
          {f._info && <div className="text-xs text-slate-500 md:col-span-2">Data dir: <span className="font-mono">{f._info.data_dir}</span> · Apps root: <span className="font-mono">{f._info.apps_root}</span> · Edge network: <span className="font-mono">{f._info.edge_network}</span></div>}
        </div>
      </Section>

      <Section title="Sao lưu panel (DB + chứng chỉ Caddy + .env)" actions={<button className="btn-secondary btn-sm" onClick={async () => { try { const r = await api.post('/api/system/panel-backup'); setJobTitle('Sao lưu panel'); setJob(r.job_id) } catch (e) { toast.error(e) } }}><Save className="h-3.5 w-3.5" />Sao lưu ngay</button>}>
        <div className="space-y-3 p-4 text-sm">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Lịch (cron)" hint="Để trống = chỉ sao lưu thủ công"><input className="input font-mono" value={f.panel_backup_cron || ''} onChange={(e) => set('panel_backup_cron', e.target.value)} placeholder="15 3 * * *" /></Field>
            <Field label="Giữ lại (bản)"><input className="input" type="number" min={1} value={f.panel_backup_keep ?? ''} onChange={(e) => set('panel_backup_keep', Number(e.target.value))} /></Field>
            <Field label="Đẩy lên đích backup" hint="Tạo đích ở trang Lưu trữ → Đích backup">
              <select className="input" value={f.panel_backup_target_id || ''} onChange={(e) => set('panel_backup_target_id', e.target.value)}>
                <option value="">— chỉ lưu cục bộ —</option>
                {(pb.data?.targets || []).map((t: any) => <option key={t.id} value={String(t.id)}>{t.name} ({t.kind})</option>)}
              </select>
            </Field>
          </div>
          {pb.data && (
            <div className="text-xs text-slate-500">
              Thư mục: <span className="font-mono">{pb.data.dir}</span> · {pb.data.files.length ? pb.data.files.slice(0, 5).map((x: any) => <span key={x.name} className="mr-3 font-mono">{x.name} ({fmtBytes(x.size)})</span>) : 'chưa có bản nào'}
            </div>
          )}
          <p className="text-xs text-slate-500">File chứa secret (.env, key TLS) — chmod 600. Cách khôi phục ghi trong <span className="font-mono">backend/app/services/panel_backup.py</span>.</p>
        </div>
      </Section>

      <Section title="Dọn dẹp Docker">
        <div className="space-y-3 p-4">
          {df.data && (
            <div className="grid gap-2 text-sm md:grid-cols-4">
              {Object.entries(df.data).map(([k, v]: any) => <div key={k} className="rounded border border-slate-200 p-2 dark:border-slate-800"><div className="text-xs text-slate-500">{k}</div><div className="font-semibold">{fmtBytes(v.size)}</div><div className="text-xs text-slate-500">{v.count} mục</div></div>)}
            </div>
          )}
          <div className="flex flex-wrap gap-4">
            <Toggle checked={prune.all_images} onChange={(v) => setPrune({ ...prune, all_images: v })} label="Xoá mọi image không dùng (không chỉ dangling)" />
            <Toggle checked={prune.volumes} onChange={(v) => setPrune({ ...prune, volumes: v })} label={<span className="text-rose-600">Xoá volume không dùng (nguy hiểm)</span>} />
          </div>
          <button className="btn-danger" onClick={async () => { try { const r = await api.post('/api/system/prune', prune); setJobTitle('Dọn dẹp Docker'); setJob(r.job_id) } catch (e) { toast.error(e) } }}><Trash className="h-4 w-4" />Dọn dẹp</button>
        </div>
      </Section>
      <JobModal jobId={job} onClose={() => { setJob(null); df.refetch(); pb.refetch() }} title={jobTitle} />
    </div>
  )
}
