export function fmtBytes(n: number | null | undefined, digits = 1): string {
  if (n == null || isNaN(n)) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (Math.abs(v) >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${u[i]}`
}
export function fmtMB(mb: number | null | undefined): string {
  if (mb == null) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`
}
export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(s)
  return d.toLocaleString('vi-VN', { hour12: false, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}
export function fmtAgo(s: string | null | undefined): string {
  if (!s) return '—'
  const sec = (Date.now() - new Date(s).getTime()) / 1000
  if (sec < 60) return `${Math.max(0, Math.round(sec))}s trước`
  if (sec < 3600) return `${Math.round(sec / 60)} phút trước`
  if (sec < 86400) return `${Math.round(sec / 3600)} giờ trước`
  return `${Math.round(sec / 86400)} ngày trước`
}
export function fmtTime(s: string | number): string {
  return new Date(s).toLocaleTimeString('vi-VN', { hour12: false, hour: '2-digit', minute: '2-digit' })
}
export function pct(a: number | null | undefined, b: number | null | undefined): number {
  if (!a || !b) return 0
  return Math.min(100, (a / b) * 100)
}
export function cls(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(' ')
}
