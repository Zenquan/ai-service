/** 客户聊天窗：登录后的普通用户入口。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Avatar, Button, Tag, Typography } from 'antd'
import { CustomerServiceOutlined, LogoutOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { Bubble, Sender } from '@ant-design/x'
import type { BubbleListProps } from '@ant-design/x'
import { useXChat } from '@ant-design/x-sdk'
import { createCustomerServiceProvider } from '../lib/chat-provider'
import type { ChatMessage } from '../lib/chat-provider'
import { api } from '../lib/api'
import AnswerView from './AnswerView'
import type { AuthUser } from '../lib/auth'

const roles: BubbleListProps['role'] = {
  assistant: {
    placement: 'start',
    avatar: <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff' }} />,
    variant: 'filled',
    contentRender: (content, info) => (
      <AnswerView msg={content as ChatMessage} status={info?.status} />
    ),
  },
  user: {
    placement: 'end',
    avatar: <Avatar icon={<UserOutlined />} style={{ background: '#0f766e' }} />,
    variant: 'filled',
    contentRender: (content) => (content as ChatMessage).text,
  },
}

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
  const { messages, setMessages, onRequest, isRequesting } = useXChat({
    provider,
    requestPlaceholder: { role: 'assistant', text: '正在检索并按资料回答…' },
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
          <Tag color={user.role === 'operator' ? 'blue' : 'green'} bordered={false}>
            {user.role === 'operator' ? '运营' : '普通用户'}
          </Tag>
          <span className="header-divider" />
          <div className="operator">
            <span className="operator-avatar"><CustomerServiceOutlined /></span>
            <span><strong>{user.display_name}</strong><small>{user.username}</small></span>
          </div>
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
            <span>AI 基于知识库回答；订单/投诉等会先由 AI 处理，必要时转人工坐席。</span>
          </div>
          {messages.length === 0 && !isRequesting ? (
            <div className="chat-empty-state">
              <Typography.Title level={3}>有什么可以帮您？</Typography.Title>
              <Typography.Paragraph>可以咨询知识库内容、查询自己的订单物流，或提交售后/投诉。</Typography.Paragraph>
            </div>
          ) : (
            <div className="chat-message-area"><Bubble.List items={items} role={roles} autoScroll /></div>
          )}
          <div className="chat-composer">
            <Sender
              className="service-sender"
              value={input}
              onChange={setInput}
              loading={isRequesting}
              onSubmit={handleSend}
              placeholder={waitingManual ? '人工坐席正在处理中，仍可继续留言…' : '请输入您的问题…'}
              submitType="enter"
            />
          </div>
        </div>
      </main>
    </div>
  )
}
