import { useEffect, useState, type ReactNode } from 'react'
import { Loader2, X, Copy, Check } from 'lucide-react'
import { cls } from '../util'

export function Spinner({ className = '' }: { className?: string }) {
  return <Loader2 className={cls('h-4 w-4 animate-spin', className)} />
}

export function Loading({ text = 'Đang tải…' }: { text?: string }) {
  return <div className="flex items-center gap-2 p-6 text-sm text-slate-500"><Spinner /> {text}</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">{error instanceof Error ? error.message : String(error)}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-8 text-center text-sm text-slate-500">{children}</div>
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <div className="mt-0.5 text-sm text-slate-500">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

const BADGE: Record<string, string> = {
  green: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  red: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
  yellow: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  blue: 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300',
  gray: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  purple: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  orange: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
}
export function Badge({ color = 'gray', children, title }: { color?: keyof typeof BADGE | string; children: ReactNode; title?: string }) {
  return <span title={title} className={cls('inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium', BADGE[color] || BADGE.gray)}>{children}</span>
}

const STATUS_COLOR: Record<string, string> = {
  running: 'green', success: 'green', up: 'green', healthy: 'green', online: 'green',
  partial: 'yellow', restarting: 'yellow', queued: 'blue', claimed: 'blue', created: 'blue', starting: 'yellow', paused: 'yellow',
  stopped: 'gray', exited: 'red', dead: 'red', failed: 'red', down: 'red', unhealthy: 'red', unknown: 'gray',
}
const STATUS_TEXT: Record<string, string> = {
  running: 'đang chạy', success: 'thành công', partial: 'một phần', stopped: 'đã dừng', exited: 'đã thoát', failed: 'thất bại',
  queued: 'chờ', claimed: 'chờ', restarting: 'restart', created: 'mới tạo', down: 'lỗi', up: 'ổn', unhealthy: 'unhealthy', healthy: 'healthy', unknown: '?',
}
export function StatusBadge({ status }: { status: string | null | undefined }) {
  const s = status || 'unknown'
  return (
    <Badge color={STATUS_COLOR[s] || 'gray'}>
      {s === 'running' || s === 'claimed' ? <span className={cls('mr-1 inline-block h-1.5 w-1.5 rounded-full', s === 'running' ? 'bg-emerald-500' : 'bg-sky-500 animate-pulse')} /> : null}
      {STATUS_TEXT[s] || s}
    </Badge>
  )
}

const SEV: Record<string, string> = { CRITICAL: 'red', HIGH: 'orange', MEDIUM: 'yellow', LOW: 'blue', INFO: 'gray', UNKNOWN: 'gray' }
export function SevBadge({ sev }: { sev: string }) {
  return <Badge color={SEV[sev] || 'gray'}>{sev}</Badge>
}
export function SevSummary({ summary }: { summary: Record<string, number> | null | undefined }) {
  if (!summary || !Object.keys(summary).length) return <Badge color="green">sạch</Badge>
  return (
    <span className="inline-flex flex-wrap gap-1">
      {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'].filter((k) => summary[k]).map((k) => <Badge key={k} color={SEV[k]}>{k[0]}{k.slice(1).toLowerCase()}: {summary[k]}</Badge>)}
    </span>
  )
}

export function Modal({ open, onClose, title, children, footer, size = 'md' }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; size?: 'sm' | 'md' | 'lg' | 'xl'
}) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  const w = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-6xl' }[size]
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-[6vh]" onMouseDown={onClose}>
      <div className={cls('card w-full shadow-xl', w)} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <div className="font-semibold">{title}</div>
          <button className="btn-ghost btn-sm" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        <div className="max-h-[75vh] overflow-y-auto p-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-800">{footer}</div>}
      </div>
    </div>
  )
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onMouseDown={onClose}>
      <div className="h-full w-full max-w-3xl overflow-y-auto bg-white shadow-xl dark:bg-slate-900" onMouseDown={(e) => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
          <div className="font-semibold">{title}</div>
          <button className="btn-ghost btn-sm" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  )
}

export function ConfirmModal({ open, onClose, onConfirm, title, message, confirmText = 'Xác nhận', danger = true, requireText, busy, children }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; message?: ReactNode; confirmText?: string
  danger?: boolean; requireText?: string; busy?: boolean; children?: ReactNode
}) {
  const [typed, setTyped] = useState('')
  useEffect(() => { if (open) setTyped('') }, [open])
  const ok = !requireText || typed === requireText
  return (
    <Modal open={open} onClose={onClose} title={title} size="sm" footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className={danger ? 'btn-danger' : 'btn-primary'} disabled={!ok || busy} onClick={onConfirm}>{busy && <Spinner />}{confirmText}</button>
    </>}>
      <div className="space-y-3 text-sm">
        {message && <div>{message}</div>}
        {children}
        {requireText && (
          <div>
            <label className="label">Nhập <b className="font-mono">{requireText}</b> để xác nhận</label>
            <input className="input font-mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
          </div>
        )}
      </div>
    </Modal>
  )
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode; hidden?: boolean }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="mb-4 flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
      {tabs.filter((t) => !t.hidden).map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={cls('-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium', value === t.id ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300')}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, children, hint, className }: { label: ReactNode; children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="label">{label}</label>
      {children}
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <span onClick={() => onChange(!checked)} className={cls('relative inline-block h-5 w-9 rounded-full transition', checked ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-700')}>
        <span className={cls('absolute top-0.5 h-4 w-4 rounded-full bg-white transition', checked ? 'left-[18px]' : 'left-0.5')} />
      </span>
      {label && <span onClick={() => onChange(!checked)}>{label}</span>}
    </label>
  )
}

export function CopyButton({ text, className = '' }: { text: string; className?: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button type="button" className={cls('btn-ghost btn-sm', className)} title="Sao chép" onClick={() => {
      navigator.clipboard?.writeText(text).then(() => { setOk(true); setTimeout(() => setOk(false), 1500) })
    }}>
      {ok ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  )
}

export function Code({ children, className = '' }: { children: string; className?: string }) {
  return (
    <div className={cls('relative', className)}>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-slate-900 p-3 pr-10 font-mono text-xs text-slate-100">{children}</pre>
      <CopyButton text={children} className="absolute right-1 top-1 text-slate-300 hover:bg-slate-700" />
    </div>
  )
}

export function ProgressBar({ value, max = 100, warn = 75, danger = 90, className = '' }: { value: number; max?: number; warn?: number; danger?: number; className?: string }) {
  const p = max ? Math.min(100, (value / max) * 100) : 0
  const color = p >= danger ? 'bg-rose-500' : p >= warn ? 'bg-amber-500' : 'bg-emerald-500'
  return (
    <div className={cls('h-1.5 w-full overflow-hidden rounded bg-slate-200 dark:bg-slate-800', className)}>
      <div className={cls('h-full rounded', color)} style={{ width: `${p}%` }} />
    </div>
  )
}

export function Stat({ label, value, sub, icon }: { label: string; value: ReactNode; sub?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between text-xs font-medium text-slate-500">{label}{icon}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function Section({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cls('card', className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
          <div className="text-sm font-semibold">{title}</div>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </div>
      )}
      <div>{children}</div>
    </div>
  )
}
