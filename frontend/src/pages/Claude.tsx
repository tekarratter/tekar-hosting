import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Bot, Send, Plus, Trash2, ChevronRight, ChevronDown, Wrench, Brain, ShieldAlert, FileText } from 'lucide-react'
import { api, wsUrl } from '../api'
import { Badge, Empty, ErrorBox, Loading, PageHeader, Spinner, Tabs } from '../components/ui'
import Subscription from './claude/Subscription'
import Mcp from './claude/Mcp'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { useIsStaff } from '../auth'
import { cls, fmtAgo, fmtDate } from '../util'

type Block =
  | { type: 'text'; text: string }
  | { type: 'thinking'; text: string }
  | { type: 'tool'; id: string; name: string; input?: any; write?: boolean; approval?: 'pending' | 'approved' | 'denied'; result?: { content: string; is_error: boolean } }
  | { type: 'error'; text: string }
interface Msg { role: 'user' | 'assistant'; text?: string; blocks?: Block[] }

function Md({ text }: { text: string }) {
  return <div className="prose-chat text-sm leading-relaxed"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>
}

function Collapsible({ icon, title, children, defaultOpen = false, tone = '' }: { icon: React.ReactNode; title: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean; tone?: string }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={cls('my-1.5 rounded-md border text-xs', tone || 'border-slate-200 dark:border-slate-700')}>
      <button className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-slate-600 dark:text-slate-300" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}{icon}{title}
      </button>
      {open && <div className="border-t border-slate-200 px-2 py-2 dark:border-slate-700">{children}</div>}
    </div>
  )
}

function ToolBlock({ b, onApprove }: { b: Extract<Block, { type: 'tool' }>; onApprove?: (id: string, ok: boolean) => void }) {
  const status = b.approval === 'pending' ? <Badge color="yellow">chờ duyệt</Badge>
    : b.approval === 'denied' ? <Badge color="red">từ chối</Badge>
    : !b.result ? <Spinner className="h-3 w-3" /> : b.result.is_error ? <Badge color="red">lỗi</Badge> : <Badge color="green">xong</Badge>
  if (b.approval === 'pending') {
    return (
      <div className="my-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/40">
        <div className="mb-1 flex items-center gap-2 font-medium text-amber-800 dark:text-amber-200"><ShieldAlert className="h-4 w-4" />Claude muốn thực hiện thao tác thay đổi hệ thống</div>
        <div className="mb-2 font-mono text-xs">{b.name}</div>
        <pre className="mb-2 max-h-48 overflow-auto rounded bg-slate-900 p-2 font-mono text-xs text-slate-100">{JSON.stringify(b.input, null, 2)}</pre>
        <div className="flex gap-2">
          <button className="btn-primary btn-sm" onClick={() => onApprove?.(b.id, true)}>Duyệt</button>
          <button className="btn-secondary btn-sm" onClick={() => onApprove?.(b.id, false)}>Từ chối</button>
        </div>
      </div>
    )
  }
  return (
    <Collapsible icon={<Wrench className="h-3.5 w-3.5" />} title={<span className="flex items-center gap-2"><span className="font-mono">{b.name}</span>{b.write && <Badge color="orange">ghi</Badge>}{status}</span>}>
      {b.input && Object.keys(b.input).length > 0 && <pre className="mb-2 overflow-auto rounded bg-slate-100 p-2 font-mono dark:bg-slate-800">{JSON.stringify(b.input, null, 2)}</pre>}
      {b.result && <pre className={cls('max-h-72 overflow-auto whitespace-pre-wrap break-all rounded p-2 font-mono', b.result.is_error ? 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300' : 'bg-slate-100 dark:bg-slate-800')}>{prettify(b.result.content)}</pre>}
    </Collapsible>
  )
}

function prettify(s: string) {
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s }
}

function Chat() {
  const toast = useToast()
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['claude-status'], queryFn: () => api.get('/api/claude/status') })
  const sessions = useQuery({ queryKey: ['claude-sessions'], queryFn: () => api.get<any[]>('/api/claude/sessions') })
  const [sid, setSid] = useState<number | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const ws = useRef<WebSocket | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const sidRef = useRef<number | null>(null)
  sidRef.current = sid

  const updateLast = (fn: (blocks: Block[]) => Block[]) => setMsgs((m) => {
    const copy = m.slice()
    let last = copy[copy.length - 1]
    if (!last || last.role !== 'assistant') { last = { role: 'assistant', blocks: [] }; copy.push(last) }
    copy[copy.length - 1] = { ...last, blocks: fn([...(last.blocks || [])]) }
    return copy
  })

  useEffect(() => {
    let closed = false
    let retry: any
    const connect = () => {
      const s = new WebSocket(wsUrl('/api/claude/ws'))
      ws.current = s
      s.onopen = () => setConnected(true)
      s.onclose = () => { setConnected(false); setBusy(false); if (!closed) retry = setTimeout(connect, 3000) }
      s.onmessage = (ev) => {
        const e = JSON.parse(ev.data)
        switch (e.type) {
          case 'session':
            if (sidRef.current !== e.id) { setSid(e.id); qc.invalidateQueries({ queryKey: ['claude-sessions'] }) }
            break
          case 'text':
            updateLast((b) => { const l = b[b.length - 1]; if (l?.type === 'text') b[b.length - 1] = { ...l, text: l.text + e.delta }; else b.push({ type: 'text', text: e.delta }); return b })
            break
          case 'thinking':
            updateLast((b) => { const l = b[b.length - 1]; if (l?.type === 'thinking') b[b.length - 1] = { ...l, text: l.text + e.delta }; else b.push({ type: 'thinking', text: e.delta }); return b })
            break
          case 'tool_start':
            updateLast((b) => [...b, { type: 'tool', id: e.id, name: e.name }])
            break
          case 'tool_use':
            updateLast((b) => { const i = b.findIndex((x) => x.type === 'tool' && x.id === e.id); const nb: Block = { type: 'tool', id: e.id, name: e.name, input: e.input, write: e.write }; if (i >= 0) b[i] = { ...(b[i] as any), ...nb }; else b.push(nb); return b })
            break
          case 'approval_request':
            updateLast((b) => b.map((x) => (x.type === 'tool' && x.id === e.id ? { ...x, input: e.input, approval: 'pending' as const } : x)))
            break
          case 'tool_result':
            updateLast((b) => b.map((x) => (x.type === 'tool' && x.id === e.id ? { ...x, result: { content: e.content, is_error: e.is_error }, approval: x.approval === 'pending' ? 'denied' as const : x.approval } : x)))
            break
          case 'error':
            updateLast((b) => [...b, { type: 'error', text: e.message }])
            break
          case 'done':
            setBusy(false)
            qc.invalidateQueries({ queryKey: ['claude-sessions'] })
            break
        }
      }
    }
    connect()
    return () => { closed = true; clearTimeout(retry); ws.current?.close() }
  }, [qc])

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs])

  async function open(id: number) {
    try {
      const r = await api.get(`/api/claude/sessions/${id}`)
      setSid(id)
      setMsgs(r.messages.map((m: any) => m.role === 'user' ? m : { role: 'assistant', blocks: m.blocks.map((b: any) => b.type === 'tool' ? { ...b, result: b.result } : b) }))
    } catch (e) { toast.error(e) }
  }

  function send() {
    const text = input.trim()
    if (!text || busy || !ws.current || ws.current.readyState !== WebSocket.OPEN) return
    setMsgs((m) => [...m, { role: 'user', text }, { role: 'assistant', blocks: [] }])
    ws.current.send(JSON.stringify({ type: 'message', session_id: sid, text }))
    setInput('')
    setBusy(true)
  }

  function approve(id: string, ok: boolean) {
    ws.current?.send(JSON.stringify({ type: 'approval', id, approved: ok }))
    updateLast((b) => b.map((x) => (x.type === 'tool' && x.id === id ? { ...x, approval: ok ? 'approved' as const : 'denied' as const } : x)))
  }

  const suggestions = ['Tình trạng hệ thống hiện tại thế nào?', 'Container nào đang lỗi hoặc restart nhiều? Tìm nguyên nhân.', 'App nào chưa có lịch backup?', 'Có vấn đề bảo mật nghiêm trọng nào cần xử lý?']

  return (
    <div className="grid h-[calc(100vh-190px)] min-h-[480px] gap-3 lg:grid-cols-[240px_1fr]">
      <div className="card flex flex-col overflow-hidden">
        <div className="border-b border-slate-200 p-2 dark:border-slate-800"><button className="btn-primary w-full justify-center" onClick={() => { setSid(null); setMsgs([]) }}><Plus className="h-4 w-4" />Cuộc trò chuyện mới</button></div>
        <div className="flex-1 overflow-y-auto p-1">
          {(sessions.data || []).map((s) => (
            <div key={s.id} className={cls('group flex cursor-pointer items-center gap-1 rounded px-2 py-1.5 text-sm', sid === s.id ? 'bg-indigo-50 dark:bg-indigo-950/50' : 'hover:bg-slate-100 dark:hover:bg-slate-800')} onClick={() => open(s.id)}>
              <div className="min-w-0 flex-1"><div className="truncate">{s.title}</div><div className="text-[11px] text-slate-400">{fmtAgo(s.updated_at)}</div></div>
              <button className="hidden group-hover:block" onClick={async (e) => { e.stopPropagation(); await api.del(`/api/claude/sessions/${s.id}`); if (sid === s.id) { setSid(null); setMsgs([]) } sessions.refetch() }}><Trash2 className="h-3.5 w-3.5 text-slate-400" /></button>
            </div>
          ))}
        </div>
      </div>
      <div className="card flex min-h-0 flex-col">
        {status.data && !status.data.configured && (
          <div className="m-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            Chưa cấu hình Claude. Đăng nhập gói Claude ở tab <Link to="/claude/subscription" className="underline">Gói Claude</Link>, hoặc admin thêm Anthropic API key tại <Link to="/settings" className="underline">Cài đặt → Claude</Link>.
          </div>
        )}
        {status.data?.configured && (
          <div className="flex items-center justify-end gap-1 border-b border-slate-200 px-3 py-1.5 text-xs text-slate-500 dark:border-slate-800">
            Đang dùng: <Badge color={status.data.engine === 'subscription' ? 'purple' : 'blue'}>{status.data.engine === 'subscription' ? 'Gói Claude (Claude Code)' : 'Claude API'}</Badge>
          </div>
        )}
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {!msgs.length && (
            <div className="mx-auto mt-10 max-w-lg text-center">
              <Bot className="mx-auto h-10 w-10 text-indigo-500" />
              <div className="mt-2 font-semibold">Trợ lý vận hành Claude</div>
              <div className="mt-1 text-sm text-slate-500">Hỏi về trạng thái, log, tài nguyên, bảo mật… Claude dùng dữ liệu thật của hệ thống. Thao tác thay đổi luôn cần bạn duyệt.</div>
              <div className="mt-4 grid gap-2">{suggestions.map((s) => <button key={s} className="btn-secondary justify-center" onClick={() => setInput(s)}>{s}</button>)}</div>
            </div>
          )}
          {msgs.map((m, i) => m.role === 'user' ? (
            <div key={i} className="flex justify-end"><div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white">{m.text}</div></div>
          ) : (
            <div key={i} className="flex gap-2">
              <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-indigo-600 dark:bg-indigo-950"><Bot className="h-4 w-4" /></div>
              <div className="min-w-0 flex-1">
                {(m.blocks || []).map((b, j) => {
                  if (b.type === 'text') return <Md key={j} text={b.text} />
                  if (b.type === 'thinking') return <Collapsible key={j} icon={<Brain className="h-3.5 w-3.5" />} title="Suy nghĩ"><div className="whitespace-pre-wrap text-slate-500">{b.text}</div></Collapsible>
                  if (b.type === 'tool') return <ToolBlock key={j} b={b} onApprove={approve} />
                  return <ErrorBox key={j} error={b.text} />
                })}
                {busy && i === msgs.length - 1 && <div className="mt-1 flex items-center gap-2 text-xs text-slate-400"><Spinner className="h-3 w-3" />Claude đang làm việc…</div>}
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <div className="border-t border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-end gap-2">
            <textarea className="input min-h-[42px] resize-none" rows={2} placeholder={connected ? 'Nhập câu hỏi… (Enter để gửi, Shift+Enter xuống dòng)' : 'Đang kết nối…'} value={input}
              onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
            <button className="btn-primary h-[42px]" disabled={!connected || busy || !input.trim()} onClick={send}>{busy ? <Spinner /> : <Send className="h-4 w-4" />}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Reports() {
  const [sel, setSel] = useState<number | null>(null)
  const [job, setJob] = useState<number | null>(null)
  const toast = useToast()
  const list = useQuery({ queryKey: ['claude-reports'], queryFn: () => api.get<any[]>('/api/claude/reports') })
  const rep = useQuery({ queryKey: ['claude-report', sel], queryFn: () => api.get(`/api/claude/reports/${sel}`), enabled: sel != null })
  useEffect(() => { if (sel == null && list.data?.length) setSel(list.data[0].id) }, [list.data, sel])
  return (
    <div className="grid gap-3 lg:grid-cols-[260px_1fr]">
      <div className="card">
        <div className="border-b border-slate-200 p-2 dark:border-slate-800">
          <button className="btn-primary w-full justify-center" onClick={async () => { try { const r = await api.post('/api/claude/reports'); setJob(r.job_id) } catch (e) { toast.error(e) } }}><FileText className="h-4 w-4" />Tạo báo cáo mới</button>
        </div>
        <div className="p-1">
          {(list.data || []).map((r) => (
            <button key={r.id} className={cls('block w-full rounded px-2 py-1.5 text-left text-sm', sel === r.id ? 'bg-indigo-50 dark:bg-indigo-950/50' : 'hover:bg-slate-100 dark:hover:bg-slate-800')} onClick={() => setSel(r.id)}>
              <div className="truncate">{r.title}</div><div className="text-[11px] text-slate-400">{fmtDate(r.created_at)}</div>
            </button>
          ))}
          {!list.data?.length && <Empty>Chưa có báo cáo</Empty>}
        </div>
      </div>
      <div className="card p-5">
        {rep.isLoading && <Loading />}
        {rep.data ? <><h2 className="mb-2 text-lg font-semibold">{rep.data.title}</h2><Md text={rep.data.content} /></> : !rep.isLoading && <Empty>Chọn hoặc tạo báo cáo. Có thể đặt lịch gửi tự động qua Telegram trong Cài đặt (report_cron).</Empty>}
      </div>
      <JobModal jobId={job} title="Claude đang viết báo cáo" onClose={() => { setJob(null); list.refetch().then((r) => r.data?.[0] && setSel(r.data[0].id)) }} />
    </div>
  )
}

export default function Claude() {
  const { section = 'chat' } = useParams()
  const nav = useNavigate()
  const staff = useIsStaff()
  return (
    <div>
      <PageHeader title="Claude" subtitle="Trợ lý vận hành, báo cáo tự động, đăng nhập gói Claude và MCP" />
      <Tabs value={section} onChange={(s) => nav(`/claude/${s}`)} tabs={[
        { id: 'chat', label: 'Trợ lý' },
        { id: 'reports', label: 'Báo cáo', hidden: !staff },
        { id: 'subscription', label: 'Gói Claude', hidden: !staff },
        { id: 'mcp', label: 'MCP' },
      ]} />
      {section === 'chat' && <Chat />}
      {section === 'reports' && staff && <Reports />}
      {(section === 'subscription' || section === 'console') && staff && <Subscription />}
      {section === 'mcp' && <Mcp />}
    </div>
  )
}
