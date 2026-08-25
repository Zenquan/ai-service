/** AI 回答渲染：Markdown 正文 + 引用来源卡片 + 错误提示 */

import { Alert, Space, Tag, Typography } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, FileTextOutlined } from '@ant-design/icons'
import { Sources, XProvider } from '@ant-design/x'
import { XMarkdown } from '@ant-design/x-markdown'
import type { Material } from '../lib/api'
import type { ChatMessage } from '../lib/chat-provider'

function AnswerView({ msg }: { msg: ChatMessage }) {
  if (msg.error) {
    return (
      <Alert
        type="error"
        showIcon
        message="这次回答失败了"
        description={msg.error}
      />
    )
  }

  // 引用素材 → Sources items（title 用文档名，description 用 chunk 文本）
  const sourceItems = (msg.materials ?? []).map((m: Material, i: number) => ({
    key: `${m.doc}:${m.seq}`,
    title: `[来源${i + 1}] ${m.doc}`,
    description: m.text,
    icon: <FileTextOutlined />,
  }))

  return (
    <div style={{ maxWidth: '100%' }}>
      {msg.text ? (
        <XMarkdown content={msg.text} />
      ) : (
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          （空回答）
        </Typography.Paragraph>
      )}

      {msg.citations && msg.citations.length > 0 && (
        <Space style={{ marginTop: 8 }} wrap>
          <Tag
            icon={msg.citationValid ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
            color={msg.citationValid ? 'success' : 'error'}
          >
            引用 {msg.citations.join(',')} · {msg.citationValid ? '校验通过' : '含越界引用'}
          </Tag>
        </Space>
      )}

      {sourceItems.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Sources
            title={`依据素材（${sourceItems.length}）`}
            items={sourceItems}
            defaultExpanded={false}
          />
        </div>
      )}
    </div>
  )
}

// Sources 组件需要在 XProvider 内才能用主题 token，导出包装
export default function AnswerViewWrapped(props: { msg: ChatMessage }) {
  return (
    <XProvider>
      <AnswerView {...props} />
    </XProvider>
  )
}