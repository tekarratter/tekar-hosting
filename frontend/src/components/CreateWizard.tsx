import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Github, Package, FileCode2, Copy, Trash2, Upload, Search, CheckCircle2, TerminalSquare, RefreshCw } from 'lucide-react'
import { api, type PlatformService } from '../api'
import { Badge, ErrorBox, Field, Loading, Modal, Spinner, Toggle } from './ui'
import { useToast } from './toast'
import { GitPicker, type GitSel } from './Git'
import CloneForm, { slugify } from './CloneForm'
import { genPassword } from './DevboxCard'

export interface Template { id: string; name: string; description: string; services: string[]; web: { service: string; port: number } | null; compose: string; env: string; custom?: boolean; shared?: boolean; can_delete?: boolean }
type Mode = 'git' | 'image' | 'compose' | 'template' | 'clone' | 'dev'

const SOURCES: { id: Mode; icon: any; title: string; desc: string }[] = [
  { id: 'git', icon: Github, title: 'Từ GitHub / Git', desc: 'Clone repo (cả private), dùng compose hoặc Dockerfile trong repo, kéo code mới & deploy lại' },
  { id: 'image', icon: Package, title: 'Từ image Docker', desc: 'Chạy một image có sẵn (Docker Hub, GHCR…) với cổng, volume và env' },
  { id: 'compose', icon: FileCode2, title: 'Compose & .env', desc: 'Dán hoặc tải lên file docker-compose.yml và .env' },
  { id: 'clone', icon: Copy, title: 'Clone app đang chạy', desc: 'Nhân bản app (staging, thử nghiệm): cấu hình, thư mục, tuỳ chọn cả dữ liệu' },
  { id: 'dev', icon: TerminalSquare, title: 'Môi trường phát triển (SSH)', desc: 'Tạo user SSH + thư mục làm việc riêng, cấp sẵn Postgres/Redis/S3 dùng chung' },
]

function imageCompose(image: string, port: string, mounts: string, cmd: string) {
  const paths = mounts.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
  const vols = paths.map((p, i) => ({ name: `data${paths.length > 1 ? i + 1 : ''}`, path: p }))
  let y = `services:\n  app:\n    image: ${image || 'nginx:alpine'}\n    restart: unless-stopped\n    env_file: .env\n`
  if (cmd.trim()) y += `    command: ${JSON.stringify(cmd.trim())}\n`
  if (port) y += `    expose:\n      - "${port}"\n`
  if (vols.length) y += `    volumes:\n${vols.map((v) => `      - ${v.name}:${v.path}\n`).join('')}volumes:\n${vols.map((v) => `  ${v.name}:\n`).join('')}`
  return y
}

function FileButton({ label, onText }: { label: string; onText: (t: string) => void }) {
  return (
    <label className="btn-secondary btn-sm cursor-pointer">
      <Upload className="h-3.5 w-3.5" />{label}
      <input type="file" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) onText(await f.text()); e.target.value = '' }} />
    </label>
  )
}

export default function CreateWizard({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: number, job: number | null) => void }) {
  const toast = useToast()
  const tpls = useQuery({ queryKey: ['templates'], queryFn: () => api.get<Template[]>('/api/apps/templates'), enabled: open })
  const svcs = useQuery({ queryKey: ['platform-services'], queryFn: () => api.get<PlatformService[]>('/api/platform/services'), enabled: open })
  const [step, setStep] = useState(0)
  const [mode, setMode] = useState<Mode>('compose')
  const [tpl, setTpl] = useState<Template | null>(null)
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [desc, setDesc] = useState('')
  const [compose, setCompose] = useState('services:\n  web:\n    image: nginx:alpine\n    restart: unless-stopped\n')
  const [env, setEnv] = useState('')
  const [vars, setVars] = useState<Record<string, string>>({})
  const [bind, setBind] = useState<Record<number, string>>({})
  const [domain, setDomain] = useState('')
  const [web, setWeb] = useState({ service: '', port: '' })
  const [deploy, setDeploy] = useState(true)
  const [busy, setBusy] = useState(false)
  // image
  const [img, setImg] = useState({ image: '', port: '', mounts: '', cmd: '' })
  // git
  const [gsel, setGsel] = useState<GitSel>({ repo: '', branch: '', credential_id: null, subdir: '' })
  const [insp, setInsp] = useState<any>(null)
  const [inspErr, setInspErr] = useState<unknown>(null)
  const [choice, setChoice] = useState<string>('')  // compose path | '__dockerfile' | '__custom'
  const [sync, setSync] = useState(true)
  const [autoPull, setAutoPull] = useState(false)
  const [filter, setFilter] = useState('')
  // dev (SSH)
  const [dev, setDev] = useState({ user: 'dev', password: '', keys: '', sudo: true })

  useEffect(() => { if (mode === 'image') { setCompose(imageCompose(img.image, img.port, img.mounts, img.cmd)); setWeb({ service: 'app', port: img.port }) } }, [img, mode])

  const reset = () => {
    setStep(0); setTpl(null); setName(''); setSlug(''); setDesc(''); setDomain(''); setVars({}); setBind({}); setEnv(''); setWeb({ service: '', port: '' })
    setInsp(null); setInspErr(null); setChoice(''); setGsel({ repo: '', branch: '', credential_id: null, subdir: '' }); setImg({ image: '', port: '', mounts: '', cmd: '' })
    onClose()
  }
  const autoName = (n: string) => { if (!name) { setName(n); setSlug(slugify(n)) } }
  const start = (m: Mode, t: Template | null = null) => {
    setMode(m); setTpl(t); setEnv(''); setDesc(''); setVars({}); setBind({}); setInsp(null); setInspErr(null); setChoice('')
    setName(t ? t.name : ''); setSlug(t ? slugify(t.name) : '')
    if (t) {
      setCompose(t.compose)
      const nb: Record<number, string> = {}
      t.services.forEach((k) => { const s = (svcs.data || []).find((x) => x.kind === k); if (s) nb[s.id] = '' })
      setBind(nb)
      setWeb(t.web ? { service: t.web.service, port: String(t.web.port) } : { service: '', port: '' })
    } else if (m === 'dev') {
      setDev({ user: 'dev', password: genPassword(), keys: '', sudo: true })
      const nb: Record<number, string> = {}
      ;['postgres', 'redis', 's3'].forEach((k) => { const s = (svcs.data || []).find((x) => x.kind === k); if (s) nb[s.id] = '' })
      setBind(nb)
      setWeb({ service: '', port: '' })
    } else if (m === 'compose') {
      setCompose('services:\n  web:\n    image: nginx:alpine\n    restart: unless-stopped\n')
    }
    setStep(1)
  }
  const tplVars = tpl ? [...new Set([...(tpl.env.match(/\$\{(\w+)\}/g) || [])].map((m) => m.slice(2, -1)))].filter((v) => v !== 'random') : []

  function pickChoice(c: string, data = insp) {
    setChoice(c)
    if (!data) return
    const item = data.composes.find((x: any) => x.path === c)
    if (item?.compose) {
      setCompose(item.compose)
      setWeb(item.web ? { service: item.web.service, port: String(item.web.port) } : { service: item.services?.[0] || '', port: '' })
    } else if (c === '__dockerfile') {
      setCompose(data.dockerfile_compose)
      setWeb({ service: 'app', port: data.expose?.[0] ? String(data.expose[0]) : '' })
    }
  }
  async function inspect() {
    setBusy(true); setInsp(null); setInspErr(null)
    try {
      const r = await api.post('/api/git/inspect', gsel)
      setInsp(r)
      setGsel((g) => ({ ...g, repo: r.repo, branch: g.branch || r.branch || '' }))
      if (r.env_example && !env) setEnv(r.env_example)
      autoName(r.repo.split('/').pop().replace(/\.git$/, ''))
      const first = r.composes.find((x: any) => x.compose)
      pickChoice(first ? first.path : r.dockerfile ? '__dockerfile' : '__custom', r)
      if (!first && !r.dockerfile) setCompose(`services:\n  app:\n    build: ./src${gsel.subdir ? '/' + gsel.subdir.replace(/^\/|\/$/g, '') : ''}\n    restart: unless-stopped\n    env_file: .env\n`)
    } catch (e) { setInspErr(e) } finally { setBusy(false) }
  }

  async function submit() {
    setBusy(true)
    try {
      const body: any = {
        name, slug, description: desc, compose_yaml: tpl ? '' : compose, env, template: tpl?.id ?? null, template_vars: vars,
        services: Object.entries(bind).map(([id, p]) => ({ service_id: Number(id), env_prefix: p })),
        domain: domain || null, deploy,
        web_service: web.service || null, web_port: web.port ? Number(web.port) : null,
      }
      if (mode === 'dev') {
        Object.assign(body, { template: 'devbox', compose_yaml: '', devbox: dev, domain: null, web_service: null, web_port: null })
      }
      if (mode === 'git') {
        const isCompose = !choice.startsWith('__')
        body.git = { ...gsel, compose_path: isCompose ? choice : null, sync_compose: isCompose && sync, auto_pull: autoPull }
      }
      const r = await api.post('/api/apps', body)
      toast.success(mode === 'git' ? 'Đã tạo app, đang clone repo' : mode === 'dev' ? 'Đã tạo môi trường phát triển' : 'Đã tạo ứng dụng')
      onCreated(r.id, r.job_id)
      reset()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  const step1Ok = !!name && !!slug && (mode !== 'git' || (!!insp && !!compose)) && (mode !== 'image' || !!img.image) && (mode !== 'dev' || (/^[a-z_][a-z0-9_-]{0,31}$/.test(dev.user) && (dev.password.length >= 8 || !!dev.keys.trim())))
  const shownTpls = useMemo(() => (tpls.data || []).filter((t) => !filter || `${t.name} ${t.description}`.toLowerCase().includes(filter.toLowerCase())), [tpls.data, filter])

  return (
    <Modal open={open} onClose={reset} title={step === 0 ? 'Tạo ứng dụng' : mode === 'clone' ? 'Clone app đang chạy' : tpl ? `Tạo từ template: ${tpl.name}` : `Tạo app · ${SOURCES.find((s) => s.id === mode)?.title || ''}`} size="lg"
      footer={step === 0 || mode === 'clone' ? <button className="btn-secondary" onClick={step === 0 ? reset : () => setStep(0)}>{step === 0 ? 'Huỷ' : 'Quay lại'}</button> : <>
        <button className="btn-secondary" onClick={() => setStep(step - 1)}>Quay lại</button>
        {step === 1 && <button className="btn-primary" disabled={!step1Ok} onClick={() => setStep(2)}>Tiếp</button>}
        {step === 2 && <button className="btn-primary" disabled={busy || (!!domain && !(web.service && web.port))} onClick={submit}>{busy && <Spinner />}Tạo{deploy ? ' & deploy' : ''}</button>}
      </>}>
      {step === 0 && (
        <div className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-2">
            {SOURCES.map((s) => (
              <button key={s.id} className="card flex gap-3 p-3 text-left hover:border-indigo-400" onClick={() => start(s.id)}>
                <s.icon className="mt-0.5 h-5 w-5 shrink-0 text-indigo-500" />
                <div><div className="font-medium">{s.title}</div><div className="text-xs text-slate-500">{s.desc}</div></div>
              </button>
            ))}
          </div>
          <div>
            <div className="mb-2 flex items-center gap-2">
              <div className="text-sm font-medium">Template</div>
              <div className="ml-auto flex items-center gap-1 rounded-md border border-slate-200 px-2 dark:border-slate-700"><Search className="h-3.5 w-3.5 text-slate-400" /><input className="w-40 bg-transparent py-1 text-sm outline-none" placeholder="Tìm template…" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
            </div>
            {tpls.isLoading && <Loading />}
            <div className="grid gap-2 sm:grid-cols-2">
              {shownTpls.map((t) => (
                <div key={t.id} role="button" tabIndex={0} className="card relative cursor-pointer p-3 text-left hover:border-indigo-400" onClick={() => start('template', t)}>
                  <div className="flex items-center gap-1 font-medium">{t.name}{t.custom && <Badge color={t.shared ? 'blue' : 'gray'}>{t.shared ? 'chia sẻ' : 'của tôi'}</Badge>}</div>
                  <div className="text-xs text-slate-500">{t.description}</div>
                  {t.services.length > 0 && <div className="mt-1 flex gap-1">{t.services.map((s) => <Badge key={s} color="purple">{s}</Badge>)}</div>}
                  {t.can_delete && <button className="btn-ghost btn-sm absolute right-1 top-1" title="Xoá template" onClick={async (e) => {
                    e.stopPropagation()
                    if (!confirm(`Xoá template ${t.name}?`)) return
                    try { await api.del(`/api/apps/templates/${t.id.split(':')[1]}`); tpls.refetch() } catch (er) { toast.error(er) }
                  }}><Trash2 className="h-3.5 w-3.5" /></button>}
                </div>
              ))}
            </div>
            <div className="mt-2 text-xs text-slate-500">Lưu app bất kỳ thành template: trang app → Cài đặt → Lưu làm template.</div>
          </div>
        </div>
      )}

      {step === 1 && mode === 'clone' && <CloneForm onCreated={(id, j) => { onCreated(id, j); reset() }} />}

      {step === 1 && mode !== 'clone' && (
        <div className="space-y-3">
          {mode === 'git' && (
            <div className="space-y-3 rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <GitPicker value={gsel} onChange={(v) => { setGsel(v); setInsp(null) }} />
              <div className="flex items-center gap-2">
                <button className="btn-primary btn-sm" disabled={busy || !gsel.repo} onClick={inspect}>{busy ? <Spinner /> : <Search className="h-3.5 w-3.5" />}Đọc repo</button>
                {insp && <span className="flex items-center gap-1 text-xs text-emerald-600"><CheckCircle2 className="h-3.5 w-3.5" />{insp.branch} @ {insp.commit.slice(0, 8)} — {insp.commit_msg}</span>}
              </div>
              <ErrorBox error={inspErr} />
              {insp && (
                <div className="space-y-2">
                  <div className="text-sm font-medium">Cách chạy</div>
                  {insp.composes.map((c: any) => (
                    <label key={c.path} className="flex items-start gap-2 text-sm">
                      <input type="radio" className="mt-1" disabled={!c.compose} checked={choice === c.path} onChange={() => pickChoice(c.path)} />
                      <span><span className="font-mono">{c.path}</span> {c.services && <span className="text-xs text-slate-500">({c.services.join(', ')}{c.builds ? ' · có build' : ''})</span>}
                        {c.error && <span className="text-xs text-rose-600"> {c.error}</span>}</span>
                    </label>
                  ))}
                  {insp.dockerfile && (
                    <label className="flex items-center gap-2 text-sm"><input type="radio" checked={choice === '__dockerfile'} onChange={() => pickChoice('__dockerfile')} />Build từ <span className="font-mono">Dockerfile</span>{insp.expose?.length ? <span className="text-xs text-slate-500">(EXPOSE {insp.expose.join(', ')})</span> : ''}</label>
                  )}
                  <label className="flex items-center gap-2 text-sm"><input type="radio" checked={choice === '__custom'} onChange={() => setChoice('__custom')} />Tự viết compose (code ở <span className="font-mono">./src</span>)</label>
                  {!insp.composes.length && !insp.dockerfile && <div className="text-xs text-amber-600">Repo không có compose hay Dockerfile — tự viết compose bên dưới.</div>}
                  {!choice.startsWith('__') && <Toggle checked={sync} onChange={setSync} label={<>Cập nhật compose theo <span className="font-mono">{choice}</span> mỗi lần kéo code (sửa compose trong panel sẽ bị ghi đè)</>} />}
                  <Toggle checked={autoPull} onChange={setAutoPull} label="Tự kéo code mới & deploy khi nhánh có commit mới (kiểm tra 5 phút/lần)" />
                </div>
              )}
            </div>
          )}

          {mode === 'image' && (
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Image" hint="vd ghcr.io/owner/app:1.2.0, nginx:1.27-alpine"><input className="input font-mono" value={img.image} onChange={(e) => {
                const base = (v: string) => v.split('/').pop()!.split(':')[0]
                if (!name || name === base(img.image)) { setName(base(e.target.value)); setSlug(slugify(base(e.target.value))) }
                setImg({ ...img, image: e.target.value })
              }} /></Field>
              <Field label="Cổng container (web)" hint="Dùng để gắn domain qua proxy"><input className="input font-mono" value={img.port} onChange={(e) => setImg({ ...img, port: e.target.value.replace(/\D/g, '') })} placeholder="3000" /></Field>
              <Field label="Thư mục cần lưu dữ liệu" hint="Đường dẫn trong container, cách nhau dấu phẩy — mỗi cái thành 1 volume"><input className="input font-mono" value={img.mounts} onChange={(e) => setImg({ ...img, mounts: e.target.value })} placeholder="/app/data" /></Field>
              <Field label="Command (tuỳ chọn)"><input className="input font-mono" value={img.cmd} onChange={(e) => setImg({ ...img, cmd: e.target.value })} /></Field>
            </div>
          )}
          {(mode !== 'git' || insp) && (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Tên"><input className="input" value={name} onChange={(e) => { setName(e.target.value); if (!slug || slug === slugify(name)) setSlug(slugify(e.target.value)) }} /></Field>
                <Field label="Slug" hint="a-z, 0-9, '-' · project compose sẽ là th-<slug>"><input className="input font-mono" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} /></Field>
              </div>
              <Field label="Mô tả"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
            </>
          )}


          {mode === 'dev' && (
            <div className="space-y-3 rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="User SSH" hint="a-z, 0-9, '_' '-' · thư mục làm việc /home/<user>/workspace"><input className="input font-mono" value={dev.user} onChange={(e) => setDev({ ...dev, user: e.target.value.toLowerCase() })} /></Field>
                <Field label="Mật khẩu" hint="Để trống = chỉ đăng nhập bằng key (khuyến nghị nếu mở ra Internet)">
                  <div className="flex gap-2"><input className="input font-mono" value={dev.password} onChange={(e) => setDev({ ...dev, password: e.target.value })} /><button className="btn-secondary" type="button" title="Sinh ngẫu nhiên" onClick={() => setDev({ ...dev, password: genPassword() })}><RefreshCw className="h-4 w-4" /></button></div>
                </Field>
              </div>
              <Field label="Public key (tuỳ chọn, mỗi dòng một key)"><textarea className="input font-mono text-xs" rows={3} value={dev.keys} onChange={(e) => setDev({ ...dev, keys: e.target.value })} placeholder="ssh-ed25519 AAAA… user@laptop" spellCheck={false} /></Field>
              <Toggle checked={dev.sudo} onChange={(v) => setDev({ ...dev, sudo: v })} label="Cho phép sudo không cần mật khẩu (cài thêm gói bằng apt)" />
              <div className="text-xs text-slate-500">Container Debian có sẵn git, python3, build-essential, psql, redis-cli; panel cấp một cổng SSH riêng (dải ở Cài đặt → Môi trường phát triển). Lần deploy đầu tiên panel build image <span className="font-mono">tekar-devbox</span>, mất vài phút.</div>
            </div>
          )}

          {mode === 'dev' ? null : tpl ? (
            <>
              {tplVars.map((v) => (
                <Field key={v} label={v}><input className="input font-mono" value={vars[v] || ''} onChange={(e) => setVars({ ...vars, [v]: e.target.value })} placeholder="${random} = sinh ngẫu nhiên" /></Field>
              ))}
              <details className="text-sm"><summary className="cursor-pointer text-slate-500">Xem compose của template</summary><pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{tpl.compose}</pre></details>
              <Field label="Biến môi trường bổ sung (.env)"><textarea className="input font-mono" rows={3} value={env} onChange={(e) => setEnv(e.target.value)} placeholder="KEY=value" /></Field>
            </>
          ) : (mode !== 'git' || insp) && (
            <>
              <Field label={<span className="flex items-center gap-2">docker-compose.yml {mode === 'compose' && <FileButton label="Tải file compose" onText={setCompose} />}</span>}
                hint={mode === 'git' ? 'Đường dẫn đã đổi sang ./src/… (code repo nằm trong thư mục src của app)' : mode === 'image' ? 'Sinh tự động từ form trên, sửa thêm được' : undefined}>
                <textarea className="input font-mono" rows={12} value={compose} onChange={(e) => setCompose(e.target.value)} spellCheck={false} />
              </Field>
              <Field label={<span className="flex items-center gap-2">.env {mode !== 'image' && <FileButton label="Tải file .env" onText={setEnv} />}{mode === 'git' && insp?.env_file && <span className="text-xs font-normal text-slate-500">điền sẵn từ {insp.env_file}</span>}</span>}>
                <textarea className="input font-mono" rows={5} value={env} onChange={(e) => setEnv(e.target.value)} spellCheck={false} placeholder="KEY=value" />
              </Field>
            </>
          )}
        </div>
      )}
      {step === 2 && (
        <div className="space-y-4">
          <div>
            <div className="mb-1 text-sm font-medium">Dịch vụ nền tảng dùng chung</div>
            <div className="mb-2 text-xs text-slate-500">Panel tạo database/user/bucket riêng và tiêm biến môi trường (DATABASE_URL, REDIS_URL, S3_*…) vào .env khi deploy.{mode === 'dev' && ' Với môi trường phát triển, các biến này có sẵn trong phiên SSH và file ~/services.env.'}</div>
            {(svcs.data || []).length === 0 && <div className="text-sm text-slate-500">Chưa có dịch vụ nền tảng nào.</div>}
            {(svcs.data || []).map((s) => (
              <div key={s.id} className="mb-1 flex items-center gap-2">
                <input type="checkbox" checked={s.id in bind} onChange={(e) => { const n = { ...bind }; if (e.target.checked) n[s.id] = ''; else delete n[s.id]; setBind(n) }} />
                <Badge color="purple">{s.kind}</Badge><span className="text-sm">{s.name}</span>
                {s.id in bind && <input className="input ml-auto w-40 font-mono" placeholder="tiền tố env (tuỳ chọn)" value={bind[s.id]} onChange={(e) => setBind({ ...bind, [s.id]: e.target.value.toUpperCase() })} />}
              </div>
            ))}
          </div>
          {mode !== 'dev' && <div className="grid gap-3 md:grid-cols-3">
            <Field label="Domain (tuỳ chọn)" hint="Tạo route proxy qua Cloudflare Tunnel"><input className="input font-mono" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="app.example.com" /></Field>
            <Field label="Service web"><input className="input font-mono" value={web.service} disabled={!!tpl?.web} onChange={(e) => setWeb({ ...web, service: e.target.value })} placeholder="web" /></Field>
            <Field label="Cổng container"><input className="input font-mono" value={web.port} disabled={!!tpl?.web} onChange={(e) => setWeb({ ...web, port: e.target.value.replace(/\D/g, '') })} placeholder="80" /></Field>
          </div>}
          <Toggle checked={deploy} onChange={setDeploy} label={mode === 'git' ? 'Build & deploy ngay sau khi clone' : 'Deploy ngay sau khi tạo'} />
        </div>
      )}
    </Modal>
  )
}
