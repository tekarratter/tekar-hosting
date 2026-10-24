import { useState } from 'react'
import { api } from '../../api'
import { Code, Section } from '../../components/ui'
import { useToast } from '../../components/toast'
import type { TabProps } from '../AppDetail'

export default function WebhookTab({ app, refetch }: TabProps) {
  const toast = useToast()
  const [token, setToken] = useState<string | null>(null)
  const url = token ? `${location.origin}/api/hooks/deploy/${token}` : ''
  return (
    <Section title="Deploy webhook (CI/CD)">
      <div className="space-y-3 p-4 text-sm">
        {app.git?.repo
          ? <p>App lấy code từ git: gọi webhook → panel kéo code mới, <span className="font-mono">docker compose up -d --build</span>. Thêm URL vào GitHub → Settings → Webhooks (Content type <span className="font-mono">application/json</span>, sự kiện <i>push</i>) hoặc GitLab → Webhooks; push vào nhánh khác <span className="font-mono">{app.git.branch || ''}</span> được bỏ qua.</p>
          : <p>Gọi POST tới URL webhook để panel pull image mới và <span className="font-mono">docker compose up -d</span>. Dùng trong GitHub Actions / GitLab CI sau khi push image.</p>}
        <p>Trạng thái: {app.has_deploy_token ? <b className="text-emerald-600">đang bật</b> : <b>chưa bật</b>}</p>
        <div className="flex gap-2">
          <button className="btn-primary" onClick={async () => { try { const r = await api.post(`/api/apps/${app.id}/deploy-token`); setToken(r.token); refetch() } catch (e) { toast.error(e) } }}>{app.has_deploy_token ? 'Tạo token mới (token cũ mất hiệu lực)' : 'Bật webhook'}</button>
          {app.has_deploy_token && <button className="btn-secondary" onClick={async () => { await api.del(`/api/apps/${app.id}/deploy-token`); setToken(null); refetch(); toast.success('Đã thu hồi') }}>Thu hồi</button>}
        </div>
        {token && (
          <>
            <div className="text-amber-600">Lưu lại URL này — chỉ hiển thị một lần.</div>
            <Code>{url}</Code>
            <div className="font-medium">Ví dụ:</div>
            <Code>{`curl -fsS -X POST ${url}`}</Code>
            <Code>{`# GitHub Actions\n- name: Deploy\n  run: curl -fsS -X POST \${{ secrets.TEKAR_DEPLOY_HOOK }}`}</Code>
          </>
        )}
      </div>
    </Section>
  )
}
