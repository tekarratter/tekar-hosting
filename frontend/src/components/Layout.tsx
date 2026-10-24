import { useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Boxes, Container, Database, Globe, Cloud, Archive, ShieldCheck, Bot, ListChecks, Bell, ScrollText,
  Users, Settings, UserCircle, LogOut, Moon, Sun, Menu, X, Server, Layers, HardDrive,
} from 'lucide-react'
import { useAuth } from '../auth'
import type { Role } from '../api'
import { cls } from '../util'

interface NavItem { to: string; label: string; icon: ReactNode; roles?: Role[] }
const STAFF: Role[] = ['admin', 'operator']
const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Tổng quan', items: [
    { to: '/', label: 'Dashboard', icon: <LayoutDashboard className="h-4 w-4" /> },
    { to: '/apps', label: 'Ứng dụng', icon: <Boxes className="h-4 w-4" /> },
    { to: '/stacks', label: 'Stack trên host', icon: <Layers className="h-4 w-4" />, roles: STAFF },
    { to: '/containers', label: 'Container', icon: <Container className="h-4 w-4" /> },
  ] },
  { group: 'Hạ tầng', items: [
    { to: '/platform', label: 'Dịch vụ nền tảng', icon: <Database className="h-4 w-4" />, roles: STAFF },
    { to: '/proxy', label: 'Proxy & Domain', icon: <Globe className="h-4 w-4" /> },
    { to: '/cloudflare', label: 'Cloudflare', icon: <Cloud className="h-4 w-4" />, roles: STAFF },
    { to: '/backups', label: 'Backup', icon: <Archive className="h-4 w-4" /> },
    { to: '/storage', label: 'Lưu trữ & đích backup', icon: <HardDrive className="h-4 w-4" />, roles: STAFF },
    { to: '/security', label: 'Bảo mật', icon: <ShieldCheck className="h-4 w-4" /> },
  ] },
  { group: 'Vận hành', items: [
    { to: '/claude', label: 'Claude', icon: <Bot className="h-4 w-4" /> },
    { to: '/jobs', label: 'Jobs', icon: <ListChecks className="h-4 w-4" /> },
    { to: '/alerts', label: 'Cảnh báo', icon: <Bell className="h-4 w-4" />, roles: STAFF },
    { to: '/audit', label: 'Nhật ký', icon: <ScrollText className="h-4 w-4" />, roles: STAFF },
  ] },
  { group: 'Hệ thống', items: [
    { to: '/users', label: 'Người dùng', icon: <Users className="h-4 w-4" />, roles: ['admin'] },
    { to: '/settings', label: 'Cài đặt', icon: <Settings className="h-4 w-4" />, roles: ['admin'] },
    { to: '/account', label: 'Tài khoản', icon: <UserCircle className="h-4 w-4" /> },
  ] },
]

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  return [dark, () => {
    const d = !dark
    document.documentElement.classList.toggle('dark', d)
    try { localStorage.setItem('th-theme', d ? 'dark' : 'light') } catch { /* ignore */ }
    setDark(d)
  }]
}

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const [dark, toggle] = useTheme()
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const role = user?.role || 'member'

  const sidebar = (
    <nav className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white"><Server className="h-4 w-4" /></div>
        <div>
          <div className="text-sm font-bold leading-tight">Tekar Hosting</div>
          <div className="text-[11px] text-slate-500">Control panel</div>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {NAV.map((g) => {
          const items = g.items.filter((i) => !i.roles || i.roles.includes(role))
          if (!items.length) return null
          return (
            <div key={g.group} className="mt-3">
              <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{g.group}</div>
              {items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === '/'} onClick={() => setOpen(false)}
                  className={({ isActive }) => cls('flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm',
                    isActive ? 'bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800')}>
                  {i.icon}{i.label}
                </NavLink>
              ))}
            </div>
          )
        })}
      </div>
      <div className="border-t border-slate-200 p-3 dark:border-slate-800">
        <div className="mb-2 truncate text-xs text-slate-500" title={user?.email}>{user?.name || user?.email} · <span className="font-medium">{role}</span></div>
        <div className="flex gap-1">
          <button className="btn-ghost btn-sm flex-1" onClick={toggle}>{dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}{dark ? 'Sáng' : 'Tối'}</button>
          <button className="btn-ghost btn-sm flex-1" onClick={logout}><LogOut className="h-3.5 w-3.5" />Thoát</button>
        </div>
      </div>
    </nav>
  )

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setOpen(false)}>
          <aside className="h-full w-64 bg-white dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col lg:pl-56">
        <header className="sticky top-0 z-20 flex items-center gap-2 border-b border-slate-200 bg-white/90 px-4 py-2 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90 lg:hidden">
          <button className="btn-ghost btn-sm" onClick={() => setOpen(!open)}>{open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}</button>
          <div className="text-sm font-semibold">Tekar Hosting</div>
        </header>
        <main key={loc.pathname.split('/')[1]} className="mx-auto w-full max-w-[1500px] flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  )
}
