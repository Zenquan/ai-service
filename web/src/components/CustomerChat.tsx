/** 客户聊天窗：登录后的普通用户入口。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Avatar, Button, Dropdown, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { ArrowRightOutlined, CustomerServiceOutlined, LogoutOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { Bubble, Sender } from '@ant-design/x'
import type { BubbleListProps } from '@ant-design/x'
import { useXChat } from '@ant-design/x-sdk'
import { createCustomerServiceProvider } from '../lib/chat-provider'
import type { ChatMessage } from '../lib/chat-provider'
import { api } from '../lib/api'
import AnswerView from './AnswerView'
import type { AuthUser } from '../lib/auth'

// 头像栏：与运营端统一品牌蓝，AI 与「我」用角色标签区分。
const roles: BubbleListProps['role'] = {
  assistant: {
    placement: 'start',
    avatar: <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff' }} />,
    variant: 'filled',
    header: <span className="bubble-role-label">AI 客服</span>,
    contentRender: (content, info) => (
      <AnswerView msg={content as ChatMessage} status={info?.status} />
    ),
  },
  user: {
    placement: 'end',
    avatar: <Avatar icon={<UserOutlined />} style={{ background: '#2563eb' }} />,
    variant: 'filled',
    header: <span className="bubble-role-label bubble-role-label-end">我</span>,
    contentRender: (content) => (content as ChatMessage).text,
  },
}

/** 空状态快捷提问：覆盖知识问答 / 订单 / 售后 / 转人工四条主链路，点按即发。 */
const QUICK_STARTERS = [
  { layer: '知识问答', title: '什么是 AI Agent？', desc: '知识库检索 · 带引用' },
  { layer: '订单查询', title: '查询我的订单物流', desc: '提供订单号即可查询' },
  { layer: '售后', title: '我要申请退款', desc: '退款 / 换货流程引导' },
  { layer: '人工客服', title: '转人工', desc: '紧急问题人工坐席接管' },
]

export default function CustomerChat({
  user,
  onLogout,
}: {
  user: AuthUser
  onLogout: () => void
}) {
  const [input, setInput] = useState('')
  const conversationId = `customer-${user.id}`
  const provider = useMemo(() => createCustomerServiceProvider(conversationId), [conversationId])
  const { messages, setMessages, onRequest, isRequesting, abort } = useXChat({
    provider,
    requestPlaceholder: { role: 'assistant', text: '正在检索并按资料回答…' },
    requestFallback: (_, { error }) => ({
      role: 'assistant' as const,
      text: '',
      error: `请求失败：${error?.message ?? '未知错误'}`,
      materials: [],
    }),
  })

  const loadHistory = useCallback(async () => {
    const history = await api.conversationMessages(conversationId)
    setMessages(history.map((msg, index) => ({
      id: `history-${conversationId}-${index}`,
      message: {
        role: msg.role,
        text: msg.content,
        materials: msg.materials ?? [],
        citations: msg.citations ?? [],
        citationValid: true,
        responseMode: msg.response_mode ?? undefined,
        needsHuman: msg.needs_human,
        handoffReason: msg.handoff_reason,
      } as ChatMessage,
      status: 'success' as const,
    })))
  }, [conversationId, setMessages])

  useEffect(() => {
    loadHistory().catch(() => { /* 新会话尚无历史 */ })
  }, [loadHistory])

  const latestAssistant = useMemo(() => {
    const found = [...messages].reverse().find(({ message }) => message.role === 'assistant')
    return found?.message as ChatMessage | undefined
  }, [messages])
  const waitingManual = latestAssistant?.responseMode === 'handoff'
    || latestAssistant?.responseMode === 'manual'

  // 用户菜单：点击头像/用户名弹出，移动端退出入口。
  const userMenu: MenuProps = {
    items: [
      { key: 'identity', label: `${user.display_name} · ${user.username}`, disabled: true },
      { type: 'divider' },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true, onClick: onLogout },
    ],
  }

  // 人工接管期间轮询刷新，普通用户能看到运营回复。
  useEffect(() => {
    if (!waitingManual) return
    const timer = window.setInterval(() => {
      loadHistory().catch(() => { /* 轮询失败保持现状 */ })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [waitingManual, loadHistory])

  const handleSend = (text: string) => {
    if (!text.trim() || isRequesting) return
    setInput('')
    onRequest({ message: text.trim() })
  }

  const items = messages.map(({ id, message, status }) => ({
    key: id,
    role: message.role,
    content: message,
    status,
    loading: status === 'loading',
  }))

  return (
    <div className="support-app customer-app">
      <header className="support-header">
        <div className="brand-lockup">
          <img className="brand-mark" src="/logo.png" alt="智应客服" />
          <div><strong>智应智能客服</strong><span>CUSTOMER SERVICE</span></div>
        </div>
        <div className="header-actions">
          <Tag color={user.role === 'operator' ? 'blue' : 'green'} variant="filled">
            {user.role === 'operator' ? '运营' : '普通用户'}
          </Tag>
          <span className="header-divider" />
          <Dropdown menu={userMenu} trigger={['click']} placement="bottomRight">
            <button type="button" className="operator" aria-label="用户菜单">
              <span className="operator-avatar"><CustomerServiceOutlined /></span>
              <span><strong>{user.display_name}</strong><small>{user.username}</small></span>
            </button>
          </Dropdown>
          <Button className="logout-button" type="text" icon={<LogoutOutlined />} onClick={onLogout}>退出</Button>
        </div>
      </header>

      <main className="chat-main customer-chat-main">
        <div className="chat-workspace">
          <div className="chat-toolbar">
            <div className="chat-title-group">
              <div className="chat-title-avatar"><CustomerServiceOutlined /></div>
              <div className="chat-title-line">
                <Typography.Title level={4}>智能客服</Typography.Title>
                <span className="chat-title-status"><i className="online-dot" />在线 · 自动接待</span>
              </div>
            </div>
          </div>
          <div className="chat-policy-bar">
            <span>AI 基于知识库回答，结论自动附带引用；订单 / 投诉等会先由 AI 处理，必要时转人工坐席。</span>
          </div>
          {messages.length === 0 && !isRequesting ? (
            <div className="chat-empty-state">
              <Typography.Title level={3}>有什么可以帮您？</Typography.Title>
              <Typography.Paragraph>可以咨询知识库内容、查询自己的订单物流，或提交售后 / 投诉。</Typography.Paragraph>
              <div className="suggestion-grid">
                {QUICK_STARTERS.map((s) => (
                  <button
                    type="button"
                    className="suggestion-card"
                    key={s.title}
                    onClick={() => handleSend(s.title)}
                  >
                    <span>
                      <i className="suggestion-layer">{s.layer}</i>
                      <strong>{s.title}</strong>
                      <small>{s.desc}</small>
                    </span>
                    <ArrowRightOutlined className="suggestion-arrow" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="chat-message-area"><Bubble.List items={items} role={roles} autoScroll /></div>
          )}
          <div className="chat-composer">
            <div className="composer-meta">
              <span><span className="composer-pulse" /> {waitingManual ? '人工坐席处理中' : 'AI 自动接待'}</span>
              <span>Enter 发送 · Shift + Enter 换行</span>
            </div>
            <Sender
              className="service-sender"
              value={input}
              onChange={setInput}
              loading={isRequesting}
              onSubmit={handleSend}
              onCancel={abort}
              placeholder={waitingManual ? '人工坐席正在处理中，仍可继续留言…' : '请输入您的问题…'}
              submitType="enter"
              autoSize={{ minRows: 1, maxRows: 6 }}
            />
          </div>
        </div>
      </main>
    </div>
  )
}
