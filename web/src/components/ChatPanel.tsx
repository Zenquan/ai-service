/** 客服对话主区：服务策略、消息流和输入作业台。 */

import { useEffect, useMemo, useState } from 'react'
import { Avatar, Button, Tag, Typography } from 'antd'
import { CheckCircleFilled, InfoCircleOutlined, RobotOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons'
import { Bubble, Sender } from '@ant-design/x'
import { useXChat } from '@ant-design/x-sdk'
import type { BubbleListProps } from '@ant-design/x'
import AnswerView from './AnswerView'
import { createCustomerServiceProvider } from '../lib/chat-provider'
import type { ChatMessage } from '../lib/chat-provider'
import { api } from '../lib/api'
import type { Material } from '../lib/api'

const SUGGESTIONS = [
  { label: '钱大妈的日清模式是什么？', description: '门店经营' },
  { label: '什么是 RAG 检索增强生成？', description: '产品知识' },
  { label: 'DeepFace 论文提出了什么？', description: '论文问答' },
]

const roles: BubbleListProps['role'] = {
  assistant: {
    placement: 'start',
    avatar: <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff' }} />,
    variant: 'filled',
    contentRender: (content) => <AnswerView msg={content as ChatMessage} />,
  },
  user: {
    placement: 'end',
    avatar: <Avatar icon={<UserOutlined />} style={{ background: '#1677ff' }} />,
    variant: 'filled',
    contentRender: (content) => (content as ChatMessage).text,
  },
}

export default function ChatPanel({
  conversationId: propConversationId,
  onMaterialsChange,
  onConversationStateChange,
}: {
  conversationId?: string | null
  onMaterialsChange?: (materials: Material[]) => void
  onConversationStateChange?: (state: {
    conversationId: string
    responseMode: ChatMessage['responseMode']
    needsHuman: boolean
    needsClarification: boolean
    handoffReason: string | null
  }) => void
}) {
  const [input, setInput] = useState('')
  // 会话 id：外部传入（选中历史会话 / 新建会话）优先；否则每次新建随机 id
  const [randomId] = useState(() => `web-${crypto.randomUUID()}`)
  const conversationId = propConversationId || randomId
  const customerProvider = useMemo(() => createCustomerServiceProvider(conversationId), [conversationId])

  const { messages, setMessages, onRequest, isRequesting, abort } = useXChat({
    provider: customerProvider,
    requestPlaceholder: { role: 'assistant', text: '正在检索并按资料回答…' },
    requestFallback: (_, { error }) => ({
      role: 'assistant' as const,
      text: '',
      error: `请求失败：${error?.message ?? '未知错误'}`,
      materials: [],
    }),
  })

  // 回填历史消息：切到已有会话时（组件按 key 重挂载），拉取该会话的历史消息展示。
  // 注意：不能用 useState 记录「已加载 id」做守卫——setState 触发 re-render 会让上一个
  // effect 的 cleanup 把 cancelled 置 true，StrictMode 下第二次 effect 又因守卫提前 return，
  // 最终请求结果被丢弃、消息永不展示。靠组件 key 重挂载天然保证「一次挂载只加载一次」。
  useEffect(() => {
    if (!conversationId) return
    let cancelled = false
    api.conversationMessages(conversationId)
      .then((history) => {
        if (cancelled) return
        const restored: ChatMessage[] = history.map((msg) => ({
          role: msg.role,
          text: msg.content,
          materials: msg.materials ?? [],
          citations: msg.citations ?? [],
          citationValid: true,
          responseMode: msg.response_mode ?? undefined,
          needsHuman: msg.needs_human,
          handoffReason: msg.handoff_reason,
        }))
        setMessages(restored.map((message, index) => ({
          id: `history-${conversationId}-${index}`,
          message,
          status: 'success' as const,
        })))
      })
      .catch(() => {
        /* 历史拉取失败（新会话无消息/网络异常）静默，不影响新消息 */
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, setMessages])

  const items = useMemo(
    () =>
      messages.map(({ id, message, status }) => ({
        key: id,
        role: message.role,
        content: message,
        loading: status === 'loading',
      })),
    [messages],
  )

  const handleSend = (val: string) => {
    if (!val.trim() || isRequesting) return
    setInput('')
    onRequest({ message: val.trim() })
  }

  const isEmpty = messages.length === 0 && !isRequesting

  useEffect(() => {
    const latest = [...messages].reverse().find(({ message }) => message.role === 'assistant')
    const latestMessage = latest?.message as ChatMessage | undefined
    onMaterialsChange?.(latestMessage?.materials ?? [])
    onConversationStateChange?.({
      conversationId,
      responseMode: latestMessage?.responseMode,
      needsHuman: latestMessage?.needsHuman ?? false,
      needsClarification: latestMessage?.needsClarification ?? false,
      handoffReason: latestMessage?.handoffReason ?? null,
    })
  }, [conversationId, messages, onConversationStateChange, onMaterialsChange])

  return (
    <div className="chat-workspace">
      <div className="chat-toolbar">
        <div className="chat-title-group">
          <div className="chat-title-avatar"><RobotOutlined /></div>
          <div className="chat-title-line">
            <Typography.Title level={4}>AI 客服助手</Typography.Title>
            <span className="chat-title-status"><i className="online-dot" />当前会话 · 自动接待</span>
          </div>
        </div>
        <div className="chat-toolbar-actions"><Tag icon={<SafetyCertificateOutlined />} color="blue">知识库优先</Tag><Button type="text" icon={<InfoCircleOutlined />} /></div>
      </div>
      <div className="chat-policy-bar"><CheckCircleFilled /><span>回答仅基于已接入资料，所有知识结论自动附带引用</span><span className="policy-spacer" /><Typography.Text type="secondary">响应策略：稳健模式</Typography.Text></div>

      {isEmpty ? (
        <div className="chat-empty-state">
          <div className="empty-orbit"><div className="empty-orbit-inner"><RobotOutlined /></div><span className="orbit-dot orbit-dot-one" /><span className="orbit-dot orbit-dot-two" /></div>
          <Typography.Title level={2}>今天想为客户解决什么？</Typography.Title>
          <Typography.Paragraph>我会先理解问题，再从知识库召回依据；涉及订单、售后或投诉时，会明确交给人工处理。</Typography.Paragraph>
          <div className="suggestion-grid">
            {SUGGESTIONS.map((suggestion) => (
              <Button key={suggestion.label} className="suggestion-card" onClick={() => handleSend(suggestion.label)}>
                <span><strong>{suggestion.label}</strong><small>{suggestion.description}</small></span><span className="suggestion-arrow">↗</span>
              </Button>
            ))}
          </div>
          <div className="empty-trust"><SafetyCertificateOutlined /> 内容经过引用校验 · 低置信度自动澄清 · 高风险自动转人工</div>
        </div>
      ) : (
        <div className="chat-message-area"><Bubble.List items={items} role={roles} autoScroll /></div>
      )}

      <div className="chat-composer">
        <div className="composer-meta"><span><span className="composer-pulse" /> AI 自动接待</span><span>Enter 发送 · Shift + Enter 换行</span></div>
        <Sender className="service-sender" value={input} onChange={setInput} loading={isRequesting} onSubmit={handleSend} onCancel={abort} placeholder="输入客户问题，开始一次新的服务…" submitType="enter" autoSize={{ minRows: 1, maxRows: 6 }} />
      </div>
    </div>
  )
}
