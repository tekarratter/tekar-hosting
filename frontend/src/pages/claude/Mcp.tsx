import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Trash2, Plug } from 'lucide-react'
import { api } from '../../api'
import { Badge, Code, CopyButton, ErrorBox, Loading, Section, Tabs, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'
import { fmtAgo, fmtDate } from '../../util'

const CLIENTS = [
  { id: 'code', label: 'Claude Code' },
  { id: 'desktop', label: 'Claude Desktop' },
  { id: 'json', label: '.mcp.json' },
  { id: 'curl', label: 'curl (kiểm tra)' },
]

function snippet(kind: string, url: string, token: string) {
  switch (kind) {
    case 'code':
      return `claude mcp add --transport http tekar-hosting ${url} \\\n  --header "Authorization: Bearer ${token}"`
    case 'desktop':
      return JSON.stringify({
        mcpServers: {
          'tekar-hosting': {
            command: 'npx',
            args: ['-y', 'mcp-remote', url, '--header', 'Authorization:${TH_AUTH}'],
            env: { TH_AUTH: `Bearer ${token}` },
          },
        },
      }, null, 2)
    case 'json':
      return JSON.stringify({ mcpServers: { 'tekar-hosting': { type: 'http', url, headers: { Authorization: `Bearer ${token}` } } } }, null, 2)
    default:
      return `curl -s ${url} -H "Authorization: Bearer ${token}" \\\n  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`
  }
}

export default function Mcp() {
  const toast = useToast()
  const q = useQuery({ queryKey: ['claude-mcp'], queryFn: () => api.get<any>('/api/claude/mcp') })
  const [name, setName] = useState('')
  const [ro, setRo] = useState(true)
  const [raw, setRaw] = useState<string | null>(null)
  const [client, setClient] = useState('code')
  const d = q.data
  const token = raw || '<token>'

  return (
    <div className="space-y-4">
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      {d && (
        <>
          <Section title={<span className="flex items-center gap-1"><Plug className="h-4 w-4" />MCP server của panel</span>}>
            <div className="space-y-3 p-4 text-sm">
              <p>Kết nối Claude Code, Claude Desktop hoặc bất kỳ MCP client nào để hỏi đáp và vận hành hệ thống bằng các tool bên dưới. Quyền theo người sở hữu token, mọi lời gọi ghi vào nhật ký.</p>
              <div className="grid gap-2 md:grid-cols-2">
                <div><div className="text-xs text-slate-500">Endpoint (Streamable HTTP)</div><div className="flex items-center gap-1 font-mono">{d.url}<CopyButton text={d.url} /></div></div>
                <div><div className="text-xs text-slate-500">Nội bộ (container cùng mạng Docker)</div><div className="font-mono">{d.internal_url}</div></div>
              </div>
            </div>
          </Section>

          <Section title="Token truy cập">
            <div className="space-y-3 p-4 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <input className="input w-72" placeholder="Tên token, vd claude-desktop-laptop" value={name} onChange={(e) => setName(e.target.value)} />
                <Toggle checked={ro} onChange={setRo} label="Chỉ đọc (không được dùng tool thay đổi hệ thống)" />
                <button className="btn-primary" disabled={!name} onClick={async () => {
                  try { const r = await api.post('/api/auth/tokens', { name, read_only: ro }); setRaw(r.token); setName(''); q.refetch() } catch (e) { toast.error(e) }
                }}><Plus className="h-4 w-4" />Tạo token</button>
              </div>
              {raw && <div className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
                <div className="text-amber-700 dark:text-amber-400">Token chỉ hiển thị một lần, các đoạn cấu hình bên dưới đã điền sẵn token này.</div>
                <Code>{raw}</Code>
              </div>}
              <table className="table">
                <thead><tr><th>Tên</th><th>Tiền tố</th><th>Quyền</th><th>Tạo</th><th>Dùng lần cuối</th><th></th></tr></thead>
                <tbody>
                  {d.tokens.map((t: any) => (
                    <tr key={t.id}>
                      <td>{t.name}</td><td className="mono">{t.prefix}…</td>
                      <td>{t.read_only ? <Badge color="green">chỉ đọc</Badge> : <Badge color="orange">đọc + thay đổi</Badge>}</td>
                      <td className="text-xs">{fmtDate(t.created_at)}</td><td className="text-xs">{fmtAgo(t.last_used_at)}</td>
                      <td className="text-right"><button className="btn-ghost btn-sm" onClick={async () => { try { await api.del(`/api/auth/tokens/${t.id}`); q.refetch() } catch (e) { toast.error(e) } }}><Trash2 className="h-3.5 w-3.5" /></button></td>
                    </tr>
                  ))}
                  {!d.tokens.length && <tr><td colSpan={6} className="text-center text-slate-500">Chưa có token</td></tr>}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Cấu hình client">
            <div className="space-y-2 p-4 text-sm">
              <Tabs value={client} onChange={setClient} tabs={CLIENTS} />
              <Code>{snippet(client, d.url, token)}</Code>
              <div className="text-xs text-slate-500">
                {client === 'code' && 'Chạy trên máy đã cài Claude Code, kiểm tra bằng /mcp trong phiên claude. Tool thay đổi hệ thống được đánh dấu destructive nên Claude Code sẽ hỏi trước khi chạy.'}
                {client === 'desktop' && 'Thêm vào claude_desktop_config.json (Settings → Developer → Edit Config), cần Node.js để chạy mcp-remote. Khởi động lại Claude Desktop.'}
                {client === 'json' && 'Đặt file .mcp.json ở thư mục dự án (Claude Code) hoặc dùng cho client hỗ trợ cấu hình HTTP.'}
                {client === 'curl' && 'Liệt kê tool mà token này được dùng.'}
              </div>
            </div>
          </Section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title={`Tool (${d.tools.length})`}>
              <div className="max-h-[480px] overflow-y-auto">
                <table className="table">
                  <tbody>
                    {d.tools.map((t: any) => (
                      <tr key={t.name}>
                        <td className="mono whitespace-nowrap align-top">{t.name}</td>
                        <td className="text-xs">
                          {t.write ? <Badge color="orange">thay đổi</Badge> : <Badge color="green">đọc</Badge>} {t.staff_only && <Badge color="purple">staff</Badge>}
                          <div className="mt-0.5 text-slate-500">{t.description}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
            <Section title="Lời gọi MCP gần đây của bạn">
              <div className="max-h-[480px] overflow-y-auto">
                <table className="table">
                  <tbody>
                    {d.calls.map((c: any) => (
                      <tr key={c.id}>
                        <td className="whitespace-nowrap text-xs">{fmtAgo(c.created_at)}</td>
                        <td className="mono">{c.target}</td>
                        <td>{c.detail?.ok === false ? <Badge color="red">lỗi</Badge> : <Badge color="green">ok</Badge>} {c.detail?.write && <Badge color="orange">thay đổi</Badge>}</td>
                        <td className="text-xs text-slate-500">{c.detail?.source === 'mcp-subscription' ? 'trợ lý (gói Claude)' : 'client MCP'}</td>
                      </tr>
                    ))}
                    {!d.calls.length && <tr><td className="text-center text-slate-500">Chưa có lời gọi</td></tr>}
                  </tbody>
                </table>
              </div>
            </Section>
          </div>
        </>
      )}
    </div>
  )
}
