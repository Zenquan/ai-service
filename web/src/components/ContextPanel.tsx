import { Card, Tag, Tooltip, Typography } from 'antd'
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
  intent,
}: {
  materials: Material[]
  conversationId?: string
  responseMode?: 'answer' | 'clarify' | 'handoff' | 'manual'
  needsHuman?: boolean
  needsClarification?: boolean
  handoffReason?: string | null
  intent?: 'greeting' | 'knowledge_question' | 'order_query' | 'after_sale' | 'complaint' | 'unknown'
}) {
  const isHandoff = needsHuman || responseMode === 'handoff'
  const isManual = responseMode === 'manual'
  const routeLabel = isManual ? '人工已回复' : isHandoff ? '等待人工接管' : needsClarification ? '等待补充信息' : 'AI 自动处理中'
  const routeColor = isManual ? 'green' : isHandoff ? 'gold' : needsClarification ? 'orange' : 'green'
  const intentLabel: Record<NonNullable<typeof intent>, string> = {
    greeting: '问候',
    knowledge_question: '知识问答',
    order_query: '订单/物流',
    after_sale: '售后',
    complaint: '投诉/高风险',
    unknown: '待澄清',
  }
  const currentIntent = intent ? intentLabel[intent] : '待判断'
  const ragActive = materials.length > 0 || (responseMode === 'answer' && intent !== 'order_query')
  const ragBadge = needsClarification && intent === 'knowledge_question'
    ? '无匹配·待澄清'
    : ragActive
      ? '已检索/已回答'
      : '待调用'
  const toolActive = intent === 'order_query'
  const toolBadge = isManual ? '已转人工处理' : needsClarification
    ? '缺订单号·待澄清'
    : toolActive
      ? isHandoff
        ? '已执行·需人工'
        : '已接入'
      : '待接入'

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

      <Card className="customer-card" variant="borderless">
        <div className="customer-card-top">
          <div className="customer-avatar">知</div>
          <div>
            <Typography.Text strong>知识库体验会话</Typography.Text>
            <Typography.Text type="secondary">访客 · 未登录</Typography.Text>
          </div>
          <Tag color={isHandoff ? 'gold' : 'green'} variant="filled">{isHandoff ? '待接管' : '在线'}</Tag>
        </div>
        <div className="customer-meta-grid">
          <div><span>会话 ID</span><strong title={conversationId}>{conversationId ? conversationId.replace('web-', '').slice(0, 12) : '创建中'}</strong></div>
          <div><span>渠道</span><strong>Web 在线咨询</strong></div>
        </div>
      </Card>

      <Card className="route-card" variant="borderless">
        <div className="card-section-heading">
          <span className="section-icon blue"><ThunderboltFilled /></span>
          <div className="card-section-copy">
            <Typography.Text strong>自动化路由</Typography.Text>
            <Typography.Text type="secondary">当前处理策略</Typography.Text>
          </div>
        </div>
        <div className={`route-line ${intent ? 'route-active' : ''}`}><CheckCircleFilled /> <span className="route-label">意图识别</span><span className="route-badge">{currentIntent}</span></div>
        <div className={`route-line ${ragActive ? 'route-active' : 'muted'}`}><CheckCircleFilled /> <span className="route-label">知识库检索（RAG）</span><span className={`route-badge ${ragActive ? '' : 'route-badge-muted'}`}>{ragBadge}</span></div>
        <div className={`route-line ${toolActive || isManual ? 'route-active' : 'muted'}`}><ClockCircleOutlined /> <span className="route-label">只读订单工具</span><span className={`route-badge ${toolActive && isHandoff ? 'route-badge-warn' : toolActive || isManual ? '' : 'route-badge-muted'}`}>{toolBadge}</span></div>
      </Card>

      <div className="context-section-title">
        <span><FileSearchOutlined /> 本轮知识依据</span>
        {materials.length > 0 ? <Tag variant="filled" color="blue">{materials.length} 条</Tag> : null}
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
              <div className="source-item-top"><Tag variant="filled" color="blue">来源 {index + 1}</Tag><small>#{material.seq + 1}</small></div>
              <Typography.Text ellipsis={{ tooltip: material.doc }} strong>{material.doc}</Typography.Text>
              <Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary">{material.text}</Typography.Paragraph>
            </div>
          ))
        )}
      </div>

      {!isHandoff && !needsClarification ? null : (
        <Card className="handoff-card" variant="borderless">
          <div className="handoff-icon"><TeamOutlined /></div>
          <div className="handoff-copy"><Typography.Text strong>{routeLabel}</Typography.Text><Typography.Text type="secondary">{isHandoff ? handoffReason ?? '当前问题已进入人工处理队列' : '请补充关键信息后继续处理'}</Typography.Text></div>
          <Tag variant="filled" color={routeColor}>{isHandoff ? '已触发' : '待补充'}</Tag>
        </Card>
      )}
    </div>
  )
}
