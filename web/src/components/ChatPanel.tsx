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

// 运营端布局反转：客户在左（"对方"位置）、运营/AI 回复在右（"自己"位置）。
// 气泡保持 antd 默认灰色填充，不额外着色。
const roles: BubbleListProps['role'] = {
  assistant: {
    placement: 'end',
    avatar: <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff' }} />,
    variant: 'filled',
    contentRender: (content, info) => (
      <AnswerView msg={content as ChatMessage} status={info?.status} />
    ),
  },
  user: {
    placement: 'start',
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
    intent?: ChatMessage['intent']
  }) => void
}) {
  const [input, setInput] = useState('')
  const [manualSending, setManualSending] = useState(false)
  // 会话 id：外部传入（选中历史会话 / 新建会话）优先；否则每次新建随机 id
  const [randomId] = useState(() => `web-${crypto.randomUUID()}`)
  const conversationId = propConversationId || randomId
  const customerProvider = useMemo(() => createCustomerServiceProvider(conversationId), [conversationId])

  const { messages, setMessages, isRequesting, abort } = useXChat({
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
        // status 必须传给 Bubble（BubbleContext），AnswerView 靠它区分流式/完成
        // 决定「思考中…」与打字机节奏；只传 loading 会丢流式状态
        status,
        loading: status === 'loading',
      })),
    [messages],
  )

  const latestAssistant = useMemo(() => {
    const found = [...messages].reverse().find(({ message }) => message.role === 'assistant')
    return found?.message as ChatMessage | undefined
  }, [messages])
  const manualMode = latestAssistant?.responseMode === 'handoff' || latestAssistant?.responseMode === 'manual'

  // 人工接管期间轮询：客户在另一端补充消息时，运营端自动更新。
  useEffect(() => {
    if (!manualMode || !conversationId) return
    const timer = window.setInterval(() => {
      api.conversationMessages(conversationId)
        .then((history) => {
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
        })
        .catch(() => { /* 轮询失败保持现状 */ })
    }, 4000)
    return () => window.clearInterval(timer)
  }, [conversationId, manualMode, setMessages])

  const handleSend = async (val: string) => {
    if (!val.trim() || isRequesting || manualSending || !manualMode) return
    const text = val.trim()
    setInput('')
    setManualSending(true)
    try {
      await api.sendManualReply(conversationId, text)
      const manualMessage: ChatMessage = {
        role: 'assistant',
        text,
        responseMode: 'manual',
        materials: [],
        citations: [],
        citationValid: true,
      }
      setMessages((current) => [
        ...current,
        { id: `manual-${crypto.randomUUID()}`, message: manualMessage, status: 'success' as const },
      ])
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `manual-error-${crypto.randomUUID()}`,
          message: {
            role: 'assistant',
            text: '',
            error: `人工回复失败：${(error as Error).message ?? '未知错误'}`,
          },
          status: 'error' as const,
        },
      ])
    } finally {
      setManualSending(false)
    }
  }

  const isEmpty = messages.length === 0 && !isRequesting

  useEffect(() => {
    onMaterialsChange?.(latestAssistant?.materials ?? [])
    onConversationStateChange?.({
      conversationId,
      responseMode: latestAssistant?.responseMode,
      needsHuman: latestAssistant?.needsHuman ?? false,
      needsClarification: latestAssistant?.needsClarification ?? false,
      handoffReason: latestAssistant?.handoffReason ?? null,
      intent: latestAssistant?.intent,
    })
  }, [conversationId, latestAssistant, onConversationStateChange, onMaterialsChange])

  return (
    <div className="chat-workspace">
      <div className="chat-toolbar">
        <div className="chat-title-group">
          <div className="chat-title-avatar"><RobotOutlined /></div>
          <div className="chat-title-line">
            <Typography.Title level={4}>{manualMode ? '人工接管会话' : 'AI 客服助手'}</Typography.Title>
            <span className="chat-title-status"><i className={manualMode ? 'online-dot' : 'online-dot'} />{manualMode ? '人工坐席在线 · 直接回复' : '当前会话 · 自动接待'}</span>
          </div>
        </div>
        <div className="chat-toolbar-actions">
          {manualMode ? <Tag icon={<UserOutlined />} color="red">人工接管</Tag> : <Tag icon={<SafetyCertificateOutlined />} color="blue">知识库优先</Tag>}
          <Button type="text" icon={<InfoCircleOutlined />} />
        </div>
      </div>
      <div className="chat-policy-bar">
        {manualMode ? (
          <>
            <CheckCircleFilled />
            <span>当前已由人工坐席接管，回复不再经过 AI 检索链路</span>
            <span className="policy-spacer" />
            <Typography.Text type="secondary">人工模式</Typography.Text>
          </>
        ) : (
          <>
            <CheckCircleFilled />
            <span>回答仅基于已接入资料，所有知识结论自动附带引用</span>
            <span className="policy-spacer" />
            <Typography.Text type="secondary">响应策略：稳健模式</Typography.Text>
          </>
        )}
      </div>

      {isEmpty ? (
        <div className="chat-empty-state">
          <div className="empty-orbit"><div className="empty-orbit-inner"><RobotOutlined /></div><span className="orbit-dot orbit-dot-one" /><span className="orbit-dot orbit-dot-two" /></div>
          <Typography.Title level={2}>等待客户发起咨询</Typography.Title>
          <Typography.Paragraph>客户通过登录端提问后，AI 会先接待；转人工的会话会出现在这里，由你直接人工回复。</Typography.Paragraph>
        </div>
      ) : (
        <div className="chat-message-area"><Bubble.List items={items} role={roles} autoScroll /></div>
      )}

      <div className="chat-composer">
        <div className="composer-meta">
          <span><span className={manualMode ? 'composer-pulse' : 'composer-pulse'} /> {manualMode ? '人工坐席回复' : 'AI 自动接待'}</span>
          <span>Enter 发送 · Shift + Enter 换行</span>
        </div>
        <Sender
          className="service-sender"
          value={input}
          onChange={setInput}
          loading={isRequesting || manualSending}
          onSubmit={handleSend}
          onCancel={abort}
          disabled={!manualMode}
          placeholder={manualMode ? '输入人工回复，回车发送…' : '客户发起后，转人工会话可在此回复'}
          submitType="enter"
          autoSize={{ minRows: 1, maxRows: 6 }}
        />
      </div>
    </div>
  )
}
