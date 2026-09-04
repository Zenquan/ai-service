import { useState } from 'react'
import {
  Avatar,
  Badge,
  Button,
  Divider,
  Input,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  BookOutlined,
  CustomerServiceOutlined,
  DatabaseOutlined,
  InboxOutlined,
  PlusOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'

type Conversation = {
  id: string
  name: string
  summary: string
  time: string
  avatar: string
  color: string
  status: 'active' | 'waiting' | 'handoff'
  unread?: number
}

const conversations: Conversation[] = [
  {
    id: 'current',
    name: '知识库体验会话',
    summary: '等待客户提问',
    time: '刚刚',
    avatar: '知',
    color: '#2563eb',
    status: 'active',
  },
  {
    id: 'order',
    name: '林女士 · 订单咨询',
    summary: '想了解订单配送进度',
    time: '09:42',
    avatar: '林',
    color: '#0f766e',
    status: 'waiting',
    unread: 2,
  },
  {
    id: 'policy',
    name: '陈先生 · 售后政策',
    summary: '已由机器人完成答复',
    time: '昨天',
    avatar: '陈',
    color: '#9333ea',
    status: 'active',
  },
  {
    id: 'handoff',
    name: '周女士 · 投诉升级',
    summary: '等待人工客服接管',
    time: '周一',
    avatar: '周',
    color: '#c2410c',
    status: 'handoff',
  },
]

export default function ConversationSidebar({
  onNewConversation,
  onOpenKnowledge,
  currentStatus = 'active',
}: {
  onNewConversation: () => void
  onOpenKnowledge: () => void
  currentStatus?: Conversation['status']
}) {
  const [activeId, setActiveId] = useState('current')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | Conversation['status']>('all')

  const visibleConversations = conversations.map((item) => item.id === 'current'
    ? {
        ...item,
        status: currentStatus,
        summary: currentStatus === 'handoff' ? '等待人工客服接管' : currentStatus === 'waiting' ? '等待补充客户信息' : 'AI 自动接待中',
      }
    : item)
  const filteredConversations = visibleConversations.filter((item) =>
    (filter === 'all' || item.status === filter) &&
    `${item.name} ${item.summary}`.toLowerCase().includes(search.toLowerCase()),
  )

  return (
    <div className="conversation-sidebar">
      <div className="sidebar-heading">
        <div>
          <Typography.Text className="eyebrow">SERVICE DESK</Typography.Text>
          <Typography.Title level={4}>工作台</Typography.Title>
        </div>
        <Tooltip title="客服设置">
          <Button type="text" shape="circle" icon={<SettingOutlined />} />
        </Tooltip>
      </div>

      <Button className="new-conversation" type="primary" block icon={<PlusOutlined />} onClick={onNewConversation}>
        新建会话
      </Button>

      <div className="queue-summary">
        <div className="queue-summary-main">
          <span className="queue-icon"><InboxOutlined /></span>
          <div>
            <Typography.Text strong>我的队列</Typography.Text>
            <Typography.Text type="secondary">AI 自动接待中</Typography.Text>
          </div>
        </div>
        <Badge count={visibleConversations.filter((item) => item.status !== 'active').length} color="#2563eb" />
      </div>

      <div className="sidebar-tabs">
        <button className={filter === 'all' ? 'is-active' : ''} type="button" onClick={() => setFilter('all')}>全部 <span>{visibleConversations.length}</span></button>
        <button className={filter === 'waiting' ? 'is-active' : ''} type="button" onClick={() => setFilter('waiting')}>待处理 <span>{visibleConversations.filter((item) => item.status === 'waiting').length}</span></button>
        <button className={filter === 'handoff' ? 'is-active' : ''} type="button" onClick={() => setFilter('handoff')}>人工接管 <span>{visibleConversations.filter((item) => item.status === 'handoff').length}</span></button>
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
        {filteredConversations.map((item) => (
          <button
            className={`conversation-item ${activeId === item.id ? 'is-selected' : ''}`}
            key={item.id}
            type="button"
            onClick={() => setActiveId(item.id)}
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
        ))}
      </div>

      <div className="sidebar-spacer" />

      <div className="sidebar-status-card">
        <div className="status-card-icon"><CustomerServiceOutlined /></div>
        <div>
          <Typography.Text strong>客服机器人在线</Typography.Text>
          <Typography.Text type="secondary">知识问答 · 自动转人工</Typography.Text>
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
