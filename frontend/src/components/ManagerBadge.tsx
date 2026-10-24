import { Badge } from './ui'

const MANAGER_COLOR: Record<string, string> = {
  dockhand: 'orange', portainer: 'blue', compose: 'gray', 'tekar-hosting': 'green', unknown: 'red', other: 'purple',
  dockge: 'purple', coolify: 'purple', komodo: 'purple', standalone: 'gray',
}

export function ManagerBadge({ m }: { m: { key: string; label: string } }) {
  return <Badge color={MANAGER_COLOR[m.key] || 'purple'}>{m.label}</Badge>
}
