import { useEffect, useState } from 'react'
import { Alert, Empty, List, Spin, Tag, Typography } from 'antd'
import { AlertOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../lib/api'
import type { EvalAlert } from '../lib/api'

const RULE_LABEL: Record<EvalAlert['rule'], string> = {
  handoff_rate: '转人工率过高',
  error_rate: '出错率过高',
  unfounded_rate: '无依据承诺率过高',
}

const RULE_COLOR: Record<EvalAlert['rule'], string> = {
  handoff_rate: 'orange',
  error_rate: 'red',
  unfounded_rate: 'gold',
}

export default function AlertsPanel({ onRefresh }: { onRefresh?: (count: number) => void }) {
  const [alerts, setAlerts] = useState<EvalAlert[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    api.recentAlerts(50)
      .then((list) => {
        setAlerts(list)
        onRefresh?.(list.length)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="alerts-panel">
      <div className="alerts-toolbar">
        <Typography.Text type="secondary">最近 50 条评测告警（阈值触发记录）</Typography.Text>
        <a className="alerts-reload" onClick={load}><ReloadOutlined spin={loading} /> 刷新</a>
      </div>

      {error ? (
        <Alert type="warning" showIcon message="告警加载失败" description={error} />
      ) : loading && alerts.length === 0 ? (
        <div className="alerts-loading"><Spin /></div>
      ) : alerts.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无告警" />
      ) : (
        <List
          size="small"
          dataSource={alerts}
          renderItem={(a) => (
            <List.Item className="alerts-item">
              <div className="alerts-item-main">
                <div className="alerts-item-title">
                  <Tag color={RULE_COLOR[a.rule]} variant="filled">{RULE_LABEL[a.rule]}</Tag>
                  <Typography.Text type="secondary" className="alerts-time">
                    {new Date(a.created_at).toLocaleString()}
                  </Typography.Text>
                </div>
                <Typography.Text className="alerts-item-desc">
                  {a.message}
                </Typography.Text>
                <Typography.Text type="secondary" className="alerts-item-meta">
                  窗口 {a.window_samples} 条
                  {a.conversation_id ? ` · 会话 ${a.conversation_id.slice(0, 12)}` : ''}
                  {a.trace_id ? ` · trace ${a.trace_id.slice(0, 8)}` : ''}
                </Typography.Text>
              </div>
            </List.Item>
          )}
        />
      )}
    </div>
  )
}

export { AlertOutlined }
