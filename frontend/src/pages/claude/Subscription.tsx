import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, AlertTriangle, LogIn, KeyRound, TerminalSquare, Square, LogOut, PlayCircle } from 'lucide-react'
import { api } from '../../api'
import { Badge, Code, ErrorBox, Field, Loading, Section, Spinner } from '../../components/ui'
import Terminal from '../../components/Terminal'
import { useToast } from '../../components/toast'
import { useAuth } from '../../auth'

const ENGINES = [
  { id: 'auto', label: 'Tự động', hint: 'Có API key → dùng API key; không có → dùng gói Claude nếu đã đăng nhập.' },
  { id: 'subscription', label: 'Gói Claude', hint: 'Trợ lý + báo cáo chạy qua Claude Code bằng gói Pro/Max/Team (tính vào hạn mức của gói).' },
  { id: 'api', label: 'API key', hint: 'Gọi Claude API bằng Anthropic API key (tính phí theo token).' },
]

export default function Subscription() {
  const toast = useToast()
  const { user } = useAuth()
  const admin = user?.role === 'admin'
  const q = useQuery({ queryKey: ['claude-sub'], queryFn: () => api.get<any>('/api/claude/subscription') })
  const d = q.data
  const [engine, setEngine] = useState('auto')
  const [model, setModel] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState('')
  const [term, setTerm] = useState<string | null>(null)
  const [test, setTest] = useState<any>(null)
  useEffect(() => { if (d) { setEngine(d.engine_setting); setModel(d.model) } }, [d])

  const auth = d?.auth || {}
  const running = d?.container?.state === 'running'
  async function save(body: any, msg = 'Đã lưu') {
    setBusy('save')
    try { await api.put('/api/claude/subscription', body); toast.success(msg); q.refetch() } catch (e) { toast.error(e) } finally { setBusy('') }
  }
  async function doTest() {
    setBusy('test'); setTest(null)
    try { setTest(await api.post('/api/claude/subscription/test')) } catch (e: any) { setTest({ ok: false, message: e.message }) } finally { setBusy('') }
  }

  return (
    <div className="space-y-4">
      <Section title="Gói Claude (Pro / Max / Team)" actions={<button className="btn-secondary btn-sm" disabled={!running || !!busy} onClick={doTest}>{busy === 'test' ? <Spinner /> : <PlayCircle className="h-3.5 w-3.5" />}Kiểm tra</button>}>
        {q.isLoading && <Loading />}
        <ErrorBox error={q.error} />
        {d && (
          <div className="space-y-4 p-4 text-sm">
            <p className="text-slate-600 dark:text-slate-400">
              Dùng gói Claude thay cho API key: panel chạy <b>Claude Code</b> (headless) trong container <span className="font-mono">{d.container.name}</span>, chỉ cho phép các tool MCP của panel (không có Bash/sửa file),
              phân quyền theo người dùng đang chat, thao tác thay đổi hệ thống vẫn chờ bạn duyệt.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <span>Container:</span>
              {!d.container.exists ? <Badge color="red">chưa tạo</Badge> : <Badge color={running ? 'green' : 'red'}>{d.container.state}</Badge>}
              {d.version && <Badge>{d.version}</Badge>}
              <span className="ml-3">Đăng nhập:</span>
              {auth.loggedIn ? <Badge color="green">đã đăng nhập{auth.authMethod ? ` · ${auth.authMethod}` : ''}</Badge> : <Badge color="yellow">chưa đăng nhập</Badge>}
              {auth.email && <span className="font-mono text-xs">{auth.email}</span>}
              {auth.subscriptionType && <Badge color="purple">{auth.subscriptionType}</Badge>}
              {d.token_configured && <Badge color="blue">token dài hạn</Badge>}
              {d.credentials_file && <Badge color="blue">đăng nhập trong container</Badge>}
              <span className="ml-3">Trợ lý đang dùng:</span>
              <Badge color={d.engine === 'subscription' ? 'purple' : 'blue'}>{d.engine === 'subscription' ? 'Gói Claude' : 'API key'}</Badge>
            </div>
            {auth.error && <div className="text-xs text-rose-600">{auth.error}</div>}
            {!d.container.exists && <Code>{'cd /data/application/tekar-hosting && docker compose --profile claude up -d th-claude'}</Code>}
            {test && (
              <div className={`flex items-start gap-1 ${test.ok ? 'text-emerald-600' : 'text-rose-600'}`}>
                {test.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
                <span>{test.ok ? `Claude trả lời "${test.message}"` : test.message}{test.duration_ms ? ` · ${(test.duration_ms / 1000).toFixed(1)}s` : ''}{test.models?.length ? ` · ${test.models.join(', ')}` : ''}</span>
              </div>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
                <div className="flex items-center gap-1 font-medium"><LogIn className="h-4 w-4" />Cách 1 — Đăng nhập trong container</div>
                <ol className="list-decimal space-y-0.5 pl-5 text-xs text-slate-600 dark:text-slate-400">
                  <li>Bấm <b>Đăng nhập</b>: terminal chạy <span className="font-mono">claude auth login</span>.</li>
                  <li>Mở link hiện ra, đăng nhập claude.ai và cấp quyền.</li>
                  <li>Dán mã xác nhận vào terminal, Enter. Phiên đăng nhập lưu trong volume <span className="font-mono">claude-home</span>.</li>
                </ol>
                <div className="flex flex-wrap gap-2">
                  <button className="btn-primary btn-sm" disabled={!running} onClick={() => setTerm(term === 'login' ? null : 'login')}><LogIn className="h-3.5 w-3.5" />{term === 'login' ? 'Đóng' : 'Đăng nhập'}</button>
                  {admin && d.credentials_file && <button className="btn-secondary btn-sm" disabled={!!busy} onClick={async () => {
                    setBusy('logout'); try { await api.post('/api/claude/subscription/logout'); toast.success('Đã đăng xuất'); q.refetch() } catch (e) { toast.error(e) } finally { setBusy('') }
                  }}><LogOut className="h-3.5 w-3.5" />Đăng xuất</button>}
                </div>
              </div>
              <div className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
                <div className="flex items-center gap-1 font-medium"><KeyRound className="h-4 w-4" />Cách 2 — Token dài hạn</div>
                <div className="text-xs text-slate-600 dark:text-slate-400">
                  Chạy <span className="font-mono">claude setup-token</span> trên máy bất kỳ đã cài Claude Code (hoặc <button className="underline" disabled={!running} onClick={() => setTerm(term === 'setup-token' ? null : 'setup-token')}>chạy tại đây</button>), dán token <span className="font-mono">sk-ant-oat…</span> vào ô dưới. Token được mã hoá trong DB.
                </div>
                {admin ? (
                  <div className="flex gap-2">
                    <input className="input font-mono" type="password" placeholder={d.token_configured ? '•••• đã lưu — dán token mới để thay' : 'sk-ant-oat01-…'} value={token} onChange={(e) => setToken(e.target.value)} />
                    <button className="btn-primary btn-sm" disabled={!token || !!busy} onClick={async () => { await save({ oauth_token: token }, 'Đã lưu token'); setToken('') }}>Lưu</button>
                    {d.token_configured && <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => save({ oauth_token: '' }, 'Đã xoá token')}>Xoá</button>}
                  </div>
                ) : <div className="text-xs text-slate-500">Chỉ admin thay đổi được.</div>}
              </div>
            </div>
            {term && <Terminal key={term} container={d.container.name} run={term} height={360} />}

            <div className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <div className="font-medium">Engine cho trợ lý & báo cáo</div>
              <div className="grid gap-2 md:grid-cols-3">
                {ENGINES.map((e) => (
                  <label key={e.id} className={`cursor-pointer rounded-md border p-2 ${engine === e.id ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40' : 'border-slate-200 dark:border-slate-800'}`}>
                    <div className="flex items-center gap-2 font-medium"><input type="radio" disabled={!admin} checked={engine === e.id} onChange={() => setEngine(e.id)} />{e.label}</div>
                    <div className="mt-0.5 text-xs text-slate-500">{e.hint}</div>
                  </label>
                ))}
              </div>
              <Field label="Model khi dùng gói Claude" hint="Trống = mặc định của gói. Ví dụ: opus, sonnet, haiku, claude-opus-5-5">
                <input className="input w-72 font-mono" list="sub-models" disabled={!admin} value={model} onChange={(e) => setModel(e.target.value)} placeholder="(mặc định)" />
                <datalist id="sub-models">{['opus', 'sonnet', 'haiku', 'claude-opus-5-5', 'claude-sonnet-5-5'].map((m) => <option key={m} value={m} />)}</datalist>
              </Field>
              {engine === 'subscription' && !auth.loggedIn && <div className="text-xs text-amber-600">Chưa đăng nhập gói Claude, trợ lý sẽ báo lỗi cho tới khi đăng nhập.</div>}
              {admin && <button className="btn-primary btn-sm" disabled={!!busy} onClick={() => save({ engine, model })}>{busy === 'save' && <Spinner />}Lưu</button>}
            </div>
            <p className="text-xs text-slate-500">Gói Claude dùng cho chính bạn/đội của bạn theo điều khoản của Anthropic; lượt dùng tính vào hạn mức của gói (giới hạn theo 5 giờ/tuần). Với tự động hoá nhiều người dùng, nên dùng API key.</p>
          </div>
        )}
      </Section>
      <Console running={running} container={d?.container?.name || 'th-claude'} />
    </div>
  )
}

function Console({ running, container }: { running: boolean; container: string }) {
  const toast = useToast()
  const [term, setTerm] = useState(false)
  const [busy, setBusy] = useState(false)
  return (
    <Section title="Claude Code console (tương tác)">
      <div className="space-y-3 p-4 text-sm">
        <p>Chạy Claude Code tương tác trong container, kết nối MCP server <span className="font-mono">tekar-hosting</span> của panel: hỏi đáp, điều tra sự cố, cấu hình container (có phân quyền + audit log).</p>
        <ol className="list-decimal space-y-1 pl-5">
          <li>Bấm <b>Thiết lập MCP</b>: panel tạo API token và ghi <span className="font-mono">~/.mcp.json</span> trong container.</li>
          <li>Mở terminal, chạy <span className="font-mono">claude</span>, chấp nhận MCP server <span className="font-mono">tekar-hosting</span>, kiểm tra bằng <span className="font-mono">/mcp</span>.</li>
        </ol>
        <div className="flex gap-2">
          <button className="btn-secondary" disabled={busy || !running} onClick={async () => { setBusy(true); try { const r = await api.post('/api/claude/console/setup'); toast.success(r.hint) } catch (e) { toast.error(e) } finally { setBusy(false) } }}>{busy && <Spinner />}Thiết lập MCP</button>
          <button className="btn-primary" disabled={!running} onClick={() => setTerm(!term)}>{term ? <Square className="h-4 w-4" /> : <TerminalSquare className="h-4 w-4" />}{term ? 'Đóng terminal' : 'Mở terminal'}</button>
        </div>
        {term && <Terminal container={container} height={520} />}
      </div>
    </Section>
  )
}
