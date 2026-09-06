import { useState } from 'react'
import {
  Avatar,
  Badge,
  Button,
  Divider,
  Input,
  Tag,
  Typography,
} from 'antd'
import {
  BookOutlined,
  CustomerServiceOutlined,
  DatabaseOutlined,
  PlusOutlined,
  SearchOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import type { ConversationSummary } from '../lib/api'

type Conversation = {
  id: string
  name: string
  summary: string
  time: string
  avatar: string
  color: string
  status: 'active' | 'waiting' | 'handoff' | 'manual'
  unread?: number
}

const AVATAR_COLORS = ['#2563eb', '#0f766e', '#9333ea', '#c2410c', '#be185d', '#4f46e5', '#0e7490', '#b45309']

/** 数据库真实会话 → 侧栏展示项 */
export function toSidebarItem(item: ConversationSummary): Conversation {
  const time = new Date(item.updated_at)
  const timeText = time.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
  const summaryMap: Record<string, string> = {
    handoff: '等待人工客服接管',
    waiting: '等待补充客户信息',
    open: 'AI 自动接待中',
    manual: '人工已回复',
  }
  // 会话标题 = 第一句话的前 N 个字（无消息时回退到转人工原因/默认名）
  const firstMessage = (item.first_message ?? '').trim()
  const MAX_TITLE_CHARS = 12
  const name = firstMessage
    ? firstMessage.length > MAX_TITLE_CHARS
      ? `${firstMessage.slice(0, MAX_TITLE_CHARS)}…`
      : firstMessage
    : item.handoff_reason
      ? `会话 · ${item.handoff_reason.slice(0, 8)}`
      : 'AI 客服会话'
  const hash = [...item.id].reduce((sum, char) => sum + char.charCodeAt(0), 0)
  return {
    id: item.id,
    name,
    summary: summaryMap[item.status] ?? 'AI 自动接待中',
    time: timeText,
    avatar: '客',
    color: AVATAR_COLORS[hash % AVATAR_COLORS.length],
    status: item.status === 'open' ? 'active' : item.status,
  }
}

export default function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNewConversation,
  onOpenKnowledge,
  currentStatus = 'active',
}: {
  conversations: ConversationSummary[]
  activeId: string
  onSelect: (conversationId: string) => void
  onNewConversation: () => void
  onOpenKnowledge: () => void
  currentStatus?: Conversation['status']
}) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'waiting' | 'handoff'>('all')

  const visibleConversations = conversations.map((item) =>
    item.id === activeId && activeId.startsWith('web-')
      ? {
          ...toSidebarItem(item),
          status: currentStatus,
          summary: currentStatus === 'handoff'
            ? '等待人工客服接管'
            : currentStatus === 'waiting'
              ? '等待补充客户信息'
              : currentStatus === 'manual'
                ? '人工已回复'
                : 'AI 自动接待中',
        }
      : toSidebarItem(item),
  )
  const filteredConversations = visibleConversations.filter((item) =>
    (filter === 'all' || (filter === 'handoff' ? (item.status === 'handoff' || item.status === 'manual') : item.status === filter)) &&
    `${item.name} ${item.summary}`.toLowerCase().includes(search.toLowerCase()),
  )

  return (
    <div className="conversation-sidebar">
      <Button className="new-conversation" type="primary" block icon={<PlusOutlined />} onClick={onNewConversation}>
        新建会话
      </Button>

      <div className="sidebar-tabs">
        <button className={filter === 'all' ? 'is-active' : ''} type="button" onClick={() => setFilter('all')}>全部 <span>{visibleConversations.length}</span></button>
        <button className={filter === 'waiting' ? 'is-active' : ''} type="button" onClick={() => setFilter('waiting')}>待处理 <span>{visibleConversations.filter((item) => item.status === 'waiting').length}</span></button>
        <button className={filter === 'handoff' ? 'is-active' : ''} type="button" onClick={() => setFilter('handoff')}>人工接管 <span>{visibleConversations.filter((item) => item.status === 'handoff' || item.status === 'manual').length}</span></button>
      </div>

      <Input
        className="conversation-search"
        allowClear
        prefix={<SearchOutlined />}
        placeholder="搜索会话或客户"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      <div className="conversation-list">
        <Typography.Text className="list-label">最近会话</Typography.Text>
        {filteredConversations.length === 0 ? (
          <Typography.Text type="secondary" className="conversation-empty">暂无会话，点上方「新建会话」开始</Typography.Text>
        ) : (
          filteredConversations.map((item) => (
          <button
            className={`conversation-item ${activeId === item.id ? 'is-selected' : ''}`}
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
          >
            <Badge dot={item.status === 'waiting'} color="#f59e0b" offset={[-2, 28]}>
              <Avatar style={{ background: item.color }}>{item.avatar}</Avatar>
            </Badge>
            <span className="conversation-copy">
              <span className="conversation-line">
                <strong>{item.name}</strong>
                <small>{item.time}</small>
              </span>
              <span className="conversation-line conversation-summary">
                <span>{item.summary}</span>
                {item.unread ? <Badge count={item.unread} color="#2563eb" /> : null}
              </span>
            </span>
          </button>
        ))
        )}
      </div>

      <div className="sidebar-spacer" />

      <div className="sidebar-status-card">
        <div className="status-card-icon"><CustomerServiceOutlined /></div>
        <div>
          <Typography.Text strong>客服机器人在线</Typography.Text>
          <Typography.Text type="secondary">知识问答 · 只读订单工具 · 自动转人工</Typography.Text>
        </div>
        <span className="online-dot" />
      </div>

      <Divider className="sidebar-divider" />
      <button className="knowledge-entry" type="button" onClick={onOpenKnowledge}>
        <span className="knowledge-entry-icon"><DatabaseOutlined /></span>
        <span>
          <strong>知识库管理</strong>
          <small>文档、切片与检索设置</small>
        </span>
        <BookOutlined />
      </button>
      <div className="sidebar-footer">
        <span><TeamOutlined /> 运营团队</span>
        <Tag bordered={false} color="blue">MVP</Tag>
      </div>
    </div>
  )
}
