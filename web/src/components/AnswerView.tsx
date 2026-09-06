/** AI 回答渲染：Markdown 正文 + 引用校验徽标 + 依据素材卡片
 *
 * 流式体验：
 * - 流式中（loading/updating）按打字机节奏逐字展示，避免 token 一次性刷屏
 * - 流式中文本尚为空 → 显示「思考中…」
 * - 完成（success）后立即补全剩余文本
 */

import { useEffect, useRef, useState } from 'react'
import { Alert, Tag } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, FileTextOutlined } from '@ant-design/icons'
import { Sources, XProvider } from '@ant-design/x'
import { XMarkdown } from '@ant-design/x-markdown'
import type { Material } from '../lib/api'
import type { ChatMessage } from '../lib/chat-provider'

type MessageStatus = 'local' | 'loading' | 'updating' | 'success' | 'error' | 'abort' | undefined

const TYPING_TICK_MS = 28

/** 打字机：把「已展示文本」逐步追赶「目标文本」。 */
function useTypewriter(targetText: string, streaming: boolean): string {
  const [display, setDisplay] = useState(streaming ? '' : targetText)
  const displayRef = useRef(display)
  displayRef.current = display

  useEffect(() => {
    if (!streaming) {
      // 完成/历史消息：直接显示全量
      setDisplay(targetText)
      return
    }
    if (displayRef.current.length >= targetText.length) return

    const timer = window.setInterval(() => {
      const current = displayRef.current
      const gap = targetText.length - current.length
      if (gap <= 0) {
        window.clearInterval(timer)
        return
      }
      // 差距大时加速（每次多释放几个字），保证不落后太多
      const step = Math.max(1, Math.ceil(gap / 14))
      setDisplay(targetText.slice(0, current.length + step))
    }, TYPING_TICK_MS)
    return () => window.clearInterval(timer)
  }, [targetText, streaming])

  return display
}

function ThinkingDots() {
  const [dots, setDots] = useState(1)
  useEffect(() => {
    const timer = window.setInterval(() => {
      setDots((prev) => (prev % 3) + 1)
    }, 400)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <span style={{ color: 'rgba(0,0,0,0.4)' }}>
      思考中{'.'.repeat(dots)}
    </span>
  )
}

function AnswerView({ msg, status }: { msg: ChatMessage; status?: MessageStatus }) {
  const streaming = status === 'loading' || status === 'updating'
  const displayText = useTypewriter(msg.text ?? '', streaming)

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

  const clarifyTitle = msg.intent === 'order_query'
    ? '订单/物流：请补充订单号'
    : msg.clarifyReason
      ? '需要补充信息'
      : '需要补充一点信息'
  const clarifyDescription = msg.clarifyReason
    ?? (msg.intent === 'order_query'
      ? '已识别到订单/物流需求，但还没有订单号。请提供订单号（例如 A00001），我会先做归属校验再查询物流。'
      : '这句话没有明确进入知识问答、订单或售后任一流程，请换个说法或补充具体问题；也可以直接说“转人工”。')

  let layerText = ''
  let layerColor = 'blue'
  if (msg.responseMode === 'manual') {
    layerText = '人工坐席回复'
    layerColor = 'green'
  } else if (msg.responseMode === 'handoff') {
    layerText = '人工接管'
    layerColor = 'gold'
  } else if (msg.responseMode === 'clarify' && msg.intent === 'order_query') {
    layerText = '订单工具 · 等待补充订单号'
    layerColor = 'orange'
  } else if (msg.responseMode === 'clarify') {
    layerText = '意图/知识不足 · 等待澄清'
    layerColor = 'orange'
  } else if (msg.toolResults?.length) {
    layerText = '只读订单工具 · 归属校验'
    layerColor = 'geekblue'
  } else if (msg.materials?.length || msg.citations?.length) {
    layerText = '知识库检索 · 引用回答'
  }

  return (
    <div style={{ maxWidth: '100%' }}>
      {layerText ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
          <Tag color={layerColor} bordered={false}>当前链路</Tag>
          <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.55)' }}>{layerText}</span>
        </div>
      ) : null}
      {msg.responseMode === 'manual' && (
        <Alert
          className="manual-answer-alert"
          type="success"
          showIcon
          message="人工坐席回复"
          description="此回复由人工客服接管发出，不经过 AI 检索链路。"
        />
      )}
      {msg.responseMode === 'handoff' && (
        <Alert
          className="handoff-answer-alert"
          type="warning"
          showIcon
          message="转人工：已停止自动处理"
          description={msg.handoffReason ?? '当前问题需要人工客服继续处理，请人工坐席接管。'}
        />
      )}
      {msg.responseMode === 'clarify' && (
        <Alert
          className="clarify-answer-alert"
          type="info"
          showIcon
          message={clarifyTitle}
          description={clarifyDescription}
        />
      )}
      {msg.toolResults?.length && msg.responseMode !== 'handoff' ? (
        <Alert
          className="tool-result-alert"
          type="info"
          showIcon
          message="只读业务工具已返回"
          description={
            msg.toolResults[0].message
            ?? (msg.toolResults[0].ok ? '查询成功' : '工具未返回可用结果')
          }
        />
      ) : null}
      {/* 正文：Markdown 排版（流式中逐字展示，空文本时思考中） */}
      <div className="rag-answer" style={{ fontSize: 14, lineHeight: 1.75 }}>
        {displayText ? (
          <XMarkdown content={displayText} />
        ) : streaming ? (
          msg.progressLabel
            ? <span style={{ color: 'rgba(0,0,0,0.45)' }}>{msg.progressLabel}</span>
            : <ThinkingDots />
        ) : (
          <span style={{ color: 'rgba(0,0,0,0.35)' }}>（空回答）</span>
        )}
      </div>

      {/* 引用校验徽标 + 依据素材（流式完成后展示） */}
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
export default function AnswerViewWrapped(props: { msg: ChatMessage; status?: MessageStatus }) {
  return (
    <XProvider>
      <AnswerView {...props} />
    </XProvider>
  )
}
