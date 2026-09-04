/** AI 回答渲染：Markdown 正文 + 引用校验徽标 + 依据素材卡片 */

import { Alert } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, FileTextOutlined } from '@ant-design/icons'
import { Sources, XProvider } from '@ant-design/x'
import { XMarkdown } from '@ant-design/x-markdown'
import type { Material } from '../lib/api'
import type { ChatMessage } from '../lib/chat-provider'

function AnswerView({ msg }: { msg: ChatMessage }) {
  if (msg.error) {
    return (
      <Alert type="error" showIcon message="这次回答失败了" description={msg.error} />
    )
  }

  const sourceItems = (msg.materials ?? []).map((m: Material, i: number) => ({
    key: `${m.doc}:${m.seq}`,
    title: `${m.doc}`,
    description: m.text,
    icon: <FileTextOutlined />,
    // 标注来源序号，供正文 [来源N] 对应
    extra: `来源 ${i + 1}`,
  }))

  return (
    <div style={{ maxWidth: '100%' }}>
      {msg.responseMode === 'handoff' && (
        <Alert
          className="handoff-answer-alert"
          type="warning"
          showIcon
          message="已进入人工接管流程"
          description={msg.handoffReason ?? '当前问题需要人工客服继续处理'}
        />
      )}
      {msg.responseMode === 'clarify' && (
        <Alert
          className="clarify-answer-alert"
          type="info"
          showIcon
          message="需要补充一点信息"
          description="请提供产品、订单或售后场景，我再继续帮您处理。"
        />
      )}
      {/* 正文：Markdown 排版 */}
      <div className="rag-answer" style={{ fontSize: 14, lineHeight: 1.75 }}>
        {msg.text ? (
          <XMarkdown content={msg.text} />
        ) : (
          <span style={{ color: 'rgba(0,0,0,0.35)' }}>（空回答）</span>
        )}
      </div>

      {/* 引用校验徽标 + 依据素材 */}
      {msg.citations && msg.citations.length > 0 && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px dashed rgba(0,0,0,0.08)' }}>
          {/* 校验状态 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            {msg.citationValid ? (
              <CheckCircleFilled style={{ color: '#52c41a', fontSize: 13 }} />
            ) : (
              <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 13 }} />
            )}
            <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.55)' }}>
              引用 {msg.citations.join(', ')} · {msg.citationValid ? '校验通过' : '含越界引用'}
            </span>
          </div>

          {/* 依据素材（可展开） */}
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

// Sources 组件需要 XProvider 上下文（主题 token）
export default function AnswerViewWrapped(props: { msg: ChatMessage }) {
  return (
    <XProvider>
      <AnswerView {...props} />
    </XProvider>
  )
}
