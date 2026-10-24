export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

function errMsg(data: any, status: number): string {
  if (!data) return `HTTP ${status}`
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail)) return data.detail.map((d: any) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
  return `HTTP ${status}`
}

export async function request<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  let data: any = null
  const text = await res.text()
  if (text) {
    try { data = JSON.parse(text) } catch { data = text }
  }
  if (res.status === 401 && !path.startsWith('/api/auth/login')) {
    onUnauthorized?.()
  }
  if (!res.ok) throw new ApiError(res.status, errMsg(data, res.status))
  return data as T
}

export const api = {
  get: <T = any>(p: string) => request<T>('GET', p),
  post: <T = any>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  put: <T = any>(p: string, b?: unknown) => request<T>('PUT', p, b ?? {}),
  del: <T = any>(p: string) => request<T>('DELETE', p),
}

export function wsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}${path}`
}

// ---- types
export type Role = 'admin' | 'operator' | 'member'
export interface User { id: number; email: string; name: string; role: Role; is_active: boolean; totp: boolean; created_at?: string; last_login_at?: string | null }
export interface Container {
  id: string; name: string; image: string; state: string; health: string | null; started_at: string; restart_count: number
  project: string | null; service: string | null; ports: { container: string; host_ip: string | null; host_port: string | null }[]
  networks: string[]; cpu_limit: number | null; mem_limit: number | null; app: string | null
}
export interface AppGit { repo: string; branch: string | null; credential_id: number | null; subdir: string; compose_path: string | null; sync_compose: boolean; auto_pull: boolean; commit?: string; commit_msg?: string; author?: string; commit_date?: string; pulled_at?: string; error?: string | null }
export interface AppT {
  id: number; slug: string; name: string; description: string; kind: 'managed' | 'adopted'; compose_project: string; work_dir: string
  compose_files: string[]; status: string; cpu_limit: number | null; mem_limit_mb: number | null; disk_quota_mb: number | null
  disk_usage_mb: number | null; service_limits: Record<string, { cpus?: number; memory_mb?: number }>; edge_services: string[]
  backup_cron: string | null; backup_retention: number; backup_target_id: number | null; backup_stop_app: boolean; backup_include_binds: boolean
  auto_update: boolean; template: string | null; owner_id: number | null; has_deploy_token: boolean; created_at: string; updated_at: string
  volumes_dir?: string | null; data_dir?: string | null; backup_destination_id?: number | null; backup_keep_local?: boolean; converted_from?: string | null
  compose_accessible?: boolean; git?: AppGit | null
  last_deployed_at: string | null; my_role: 'owner' | 'deployer' | 'viewer' | null
  containers?: any; running?: number; available_services?: string[]; compose_yaml?: string; env?: string; compose_sources?: Record<string, string>
}
export interface Job { id: number; kind: string; status: string; app_id: number | null; params: any; result: any; created_at: string; started_at: string | null; finished_at: string | null; log?: string }
export type RouteKind = 'proxy' | 'redirect' | 'dead'
export interface Route {
  id: number; app_id: number | null; app_name?: string | null; kind: RouteKind; access_list_id: number | null; certificate_id: number | null; hosts: string[]; path_prefix: string; upstreams: string[]; tls_mode: string
  cf_tunnel: boolean; enabled: boolean; priority: number; options: Record<string, any>; health_path: string | null; health_status: string | null
  health_checked_at: string | null; note: string; source: string; created_at: string
}
export interface PlatformService {
  id: number; name: string; kind: 'postgres' | 'redis' | 's3'; mode?: string; host?: string; port?: number; public_host?: string | null
  public_port?: number | null; admin_user?: string; extra?: Record<string, any>; container_name?: string | null; bindings?: number
}
export interface Backup { id: number; app_id: number; app_name: string | null; status: string; trigger: string; path: string; remote_key: string | null; size_bytes: number; items: any[]; error: string | null; started_at: string; finished_at: string | null }

export interface AccessList { id: number; name: string; satisfy_any: boolean; pass_auth: boolean; auth: { username: string }[]; clients: { address: string; directive: 'allow' | 'deny' }[]; used_by: number; created_at?: string }
export interface CustomCert { id: number; name: string; domains: string[]; issuer: string; not_after: string | null; days_left: number | null; used_by: number; created_at?: string }
export interface ManagedCert { name: string; issuer: string; not_after: string; days_left: number }
export interface Stream { id: number; listen_port: number; protocol: 'tcp' | 'udp' | 'both'; upstream: string; enabled: boolean; app_id: number | null; note: string; created_at?: string }
export interface HostStats { requests: number; '4xx': number; '5xx': number; avg_ms: number; last: number | null }
export interface BackupTarget { id: number; name: string; kind: 'local' | 's3' | 'sftp'; config: Record<string, any>; has_secret?: boolean; apps?: number; created_at?: string }
export interface StackInfo {
  project: string; work_dir: string; host_work_dir: string | null; config_files: string[]
  containers: { name: string; state: string; service: string | null }[]; running: number; images: string[]
  manager: { key: string; label: string; container?: string }
  panel: { app_id: number; slug: string; kind: 'managed' | 'adopted'; converted_from: string | null } | null
}
