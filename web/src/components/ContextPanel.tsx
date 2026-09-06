import { Card, Progress, Tag, Tooltip, Typography } from 'antd'
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  ThunderboltFilled,
} from '@ant-design/icons'
import type { Material } from '../lib/api'

export default function ContextPanel({
  materials,
  conversationId,
  responseMode = 'answer',
  needsHuman = false,
  needsClarification = false,
  handoffReason,
}: {
  materials: Material[]
  conversationId?: string
  responseMode?: 'answer' | 'clarify' | 'handoff'
  needsHuman?: boolean
  needsClarification?: boolean
  handoffReason?: string | null
}) {
  const isHandoff = needsHuman || responseMode === 'handoff'
  const routeLabel = isHandoff ? '等待人工接管' : needsClarification ? '等待补充信息' : 'AI 自动处理中'
  const routeColor = isHandoff ? 'gold' : needsClarification ? 'orange' : 'green'

  return (
    <div className="context-panel">
      <div className="context-heading">
        <div>
          <Typography.Text className="eyebrow">LIVE CONTEXT</Typography.Text>
          <Typography.Title level={4}>会话上下文</Typography.Title>
        </div>
        <Tooltip title="上下文仅用于当前会话">
          <SafetyCertificateOutlined className="context-safe-icon" />
        </Tooltip>
      </div>

      <Card className="customer-card" bordered={false}>
        <div className="customer-card-top">
          <div className="customer-avatar">知</div>
          <div>
            <Typography.Text strong>知识库体验会话</Typography.Text>
            <Typography.Text type="secondary">访客 · 未登录</Typography.Text>
          </div>
          <Tag color={isHandoff ? 'gold' : 'green'} bordered={false}>{isHandoff ? '待接管' : '在线'}</Tag>
        </div>
        <div className="customer-meta-grid">
          <div><span>会话 ID</span><strong title={conversationId}>{conversationId ? conversationId.replace('web-', '').slice(0, 12) : '创建中'}</strong></div>
          <div><span>渠道</span><strong>Web 在线咨询</strong></div>
        </div>
      </Card>

      <Card className="route-card" bordered={false}>
        <div className="card-section-heading">
          <span className="section-icon blue"><ThunderboltFilled /></span>
          <div className="card-section-copy">
            <Typography.Text strong>自动化路由</Typography.Text>
            <Typography.Text type="secondary">当前处理策略</Typography.Text>
          </div>
        </div>
        <div className="route-line"><CheckCircleFilled /> <span className="route-label">意图识别</span><span className="route-badge">已启用</span></div>
        <div className="route-line"><CheckCircleFilled /> <span className="route-label">混合检索 + RRF</span><span className="route-badge">已启用</span></div>
        <div className={`route-line ${isHandoff ? 'route-active' : ''}`}><ClockCircleOutlined /> <span className="route-label">订单查询工具</span><span className={`route-badge ${isHandoff ? 'route-badge-warn' : ''}`}>{isHandoff ? '需人工' : '只读已接入'}</span></div>
        <Progress className="route-progress" percent={isHandoff ? 100 : 75} showInfo={false} strokeColor={isHandoff ? '#e59b2e' : '#2563eb'} trailColor="#e8edf5" size="small" />
      </Card>

      <div className="context-section-title">
        <span><FileSearchOutlined /> 本轮知识依据</span>
        {materials.length > 0 ? <Tag bordered={false} color="blue">{materials.length} 条</Tag> : null}
      </div>
      <div className="source-list">
        {materials.length === 0 ? (
          <div className="source-empty">
            <FileSearchOutlined />
            <span>发送问题后，这里会显示召回片段与引用来源</span>
          </div>
        ) : (
          materials.slice(0, 3).map((material, index) => (
            <div className="source-item" key={`${material.doc}:${material.seq}`}>
              <div className="source-item-top"><Tag bordered={false} color="blue">来源 {index + 1}</Tag><small>#{material.seq + 1}</small></div>
              <Typography.Text ellipsis={{ tooltip: material.doc }} strong>{material.doc}</Typography.Text>
              <Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary">{material.text}</Typography.Paragraph>
            </div>
          ))
        )}
      </div>

      {!isHandoff && !needsClarification ? null : (
        <Card className="handoff-card" bordered={false}>
          <div className="handoff-icon"><TeamOutlined /></div>
          <div className="handoff-copy"><Typography.Text strong>{routeLabel}</Typography.Text><Typography.Text type="secondary">{isHandoff ? handoffReason ?? '当前问题已进入人工处理队列' : '请补充关键信息后继续处理'}</Typography.Text></div>
          <Tag bordered={false} color={routeColor}>{isHandoff ? '已触发' : '待补充'}</Tag>
        </Card>
      )}
    </div>
  )
}
