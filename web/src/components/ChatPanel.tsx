/** 对话面板：Welcome + Prompts（空态）→ Bubble.List + Sender（问答区） */

import { useMemo, useState } from 'react'
import { Avatar, Flex } from 'antd'
import { RobotOutlined, UserOutlined } from '@ant-design/icons'
import { Bubble, Prompts, Sender, Welcome } from '@ant-design/x'
import { useXChat } from '@ant-design/x-sdk'
import type { BubbleListProps } from '@ant-design/x'
import AnswerView from './AnswerView'
import { ragProvider } from '../lib/chat-provider'
import type { ChatMessage } from '../lib/chat-provider'

const SUGGESTIONS = [
  { key: '1', label: '钱大妈的日清模式是什么？', description: '门店经营' },
  { key: '2', label: '什么是 RAG 检索增强生成？', description: 'RAG 原理' },
  { key: '3', label: 'DeepFace 论文提出了什么？', description: '论文问答' },
  { key: '4', label: 'ResNet 的核心创新是什么？', description: '论文问答' },
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
    avatar: <Avatar icon={<UserOutlined />} style={{ background: '#52c41a' }} />,
    variant: 'outlined',
    contentRender: (content) => (content as ChatMessage).text,
  },
}

export default function ChatPanel() {
  const [input, setInput] = useState('')

  const { messages, onRequest, isRequesting, abort } = useXChat({
    provider: ragProvider,
    requestPlaceholder: { role: 'assistant', text: '正在检索并按资料回答…' },
    requestFallback: (_, { error }) => ({
      role: 'assistant' as const,
      text: '',
      error: `请求失败：${error?.message ?? '未知错误'}`,
      materials: [],
    }),
  })

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
    onRequest({ query: val.trim() })
  }

  const isEmpty = messages.length === 0

  return (
    <Flex vertical style={{ height: '100%', padding: '16px 24px' }} gap={16}>
      {isEmpty ? (
        <Flex vertical align="center" justify="center" style={{ flex: 1 }} gap={24}>
          <Welcome
            variant="borderless"
            title="RAG 知识问答助手"
            description="基于本地知识库回答——混合检索 + rerank 精排 + 引用校验，回答可溯源"
          />
          <Prompts
            title="试试这些问题："
            wrap
            items={SUGGESTIONS}
            onItemClick={(info) => handleSend(String(info.data.label))}
          />
        </Flex>
      ) : (
        <div style={{ flex: 1, minHeight: 0 }}>
          <Bubble.List
            items={items}
            role={roles}
            autoScroll
            style={{ height: '100%' }}
          />
        </div>
      )}

      <Sender
        value={input}
        onChange={setInput}
        loading={isRequesting}
        onSubmit={handleSend}
        onCancel={abort}
        placeholder="输入问题，基于知识库回答…（Enter 发送 / Shift+Enter 换行）"
        submitType="enter"
        autoSize={{ minRows: 1, maxRows: 6 }}
      />
    </Flex>
  )
}