/** 聊天消息模型 + ChatProvider（对接客服会话与兼容问答接口） */

import { DefaultChatProvider, XRequest } from '@ant-design/x-sdk'
import type { SSEOutput } from '@ant-design/x-sdk'
import type { Material, ToolResultRecord } from './api'
import { getAuthToken } from './auth'

/** 一条聊天消息：用户问题 or AI 回答（含引用素材） */
export interface ChatMessage {
  role: 'user' | 'assistant'
  /** 用户输入 or AI 回答（Markdown 文本） */
  text: string
  /** AI 回答的引用素材 */
  materials?: Material[]
  /** 引用的素材序号 [来源N] */
  citations?: number[]
  /** 引用是否合法（防幻觉校验结果） */
  citationValid?: boolean
  /** 错误信息（检索/生成失败时） */
  error?: string
  responseMode?: 'answer' | 'clarify' | 'handoff' | 'manual'
  needsHuman?: boolean
  needsClarification?: boolean
  handoffReason?: string | null
  /** 服务端识别出的意图 */
  intent?: 'greeting' | 'knowledge_question' | 'order_query' | 'after_sale' | 'complaint' | 'unknown'
  /** 澄清原因，用于给用户具体下一步 */
  clarifyReason?: string
  /** 流式过程中的阶段文案（正在检索/正在查询工具），不进入最终正文 */
  progressLabel?: string
  /** 图内工具执行结果（只读订单等） */
  toolResults?: ToolResultRecord[]
}

/** 请求入参：问题文本 */
export interface AskInput {
  query: string
}

export interface CustomerMessageInput {
  message: string
}

/** 后端响应体（透传 AskResult） */
export type AskOutput = {
  query: string
  materials: Material[]
  answer: string
  citations: number[]
  citation_valid: boolean
  material_count: number
  error: string | null
}


/**
 * DefaultChatProvider：透传后端 JSON（非流式）。
 * transformParams 把请求体固定为 { query }；
 * transformMessage 把后端响应体组装成 ChatMessage（AI 回答 + 素材）。
 */
export const ragProvider = new DefaultChatProvider<ChatMessage, AskInput, AskOutput>({
  request: XRequest<AskInput, AskOutput>('/api/v1/ask', {
    manual: true,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // 非流式：一次拿到完整响应 JSON
  }),
})

export function createCustomerServiceProvider(conversationId: string) {
  const authHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAuthToken()
  if (token) {
    authHeaders.Authorization = `Bearer ${token}`
  }
  const provider = new DefaultChatProvider<ChatMessage, CustomerMessageInput, SSEOutput>({
    request: XRequest<CustomerMessageInput, SSEOutput>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
      {
        manual: true,
        method: 'POST',
        headers: authHeaders,
      },
    ),
  })

  provider.transformParams = (requestParams, _options) => ({
    message: (requestParams as Partial<CustomerMessageInput>).message ?? '',
  })

  provider.transformLocalMessage = (requestParams) => ({
    role: 'user',
    text: (requestParams as Partial<CustomerMessageInput>).message ?? '',
  })

  provider.transformMessage = (info) => {
    const { chunk, originMessage } = info
    const base = (originMessage ?? { role: 'assistant', text: '', materials: [], citations: [] }) as ChatMessage
    if (!chunk) {
      return { ...base }
    }
    // SSE 流式 chunk：{event: 'token'|'materials'|'done'|'meta'|'error', data: <JSON字符串>}
    const event = (chunk as { event?: string }).event
    const rawData = (chunk as { data?: unknown }).data
    let data: Record<string, unknown> = {}
    if (typeof rawData === 'string') {
      try {
        data = JSON.parse(rawData) as Record<string, unknown>
      } catch {
        data = {}
      }
    } else if (rawData && typeof rawData === 'object') {
      data = rawData as Record<string, unknown>
    }
    switch (event) {
      case 'materials':
        return { ...base, materials: (data.materials as Material[]) ?? [], text: base.text ?? '' }
      case 'token':
        return { ...base, text: `${base.text ?? ''}${data.text ?? ''}`, progressLabel: undefined }
      case 'done':
        if (data.error) {
          return { ...base, error: data.error as string }
        }
        return {
          ...base,
          text: (data.answer as string) ?? base.text,
          citations: (data.citations as number[]) ?? [],
          citationValid: !!data.citation_valid,
        }
      case 'meta':
        // 图结果（工具回答 / 澄清 / 转人工）：完整回答一次到位
        return {
          role: 'assistant',
          text: (data.answer as string) ?? '',
          materials: (data.materials as Material[]) ?? [],
          citations: (data.citations as number[]) ?? [],
          citationValid: !!data.citation_valid,
          responseMode: data.response_mode as ChatMessage['responseMode'],
          needsHuman: !!data.needs_human,
          needsClarification: !!data.needs_clarification,
          handoffReason: (data.handoff_reason as string) ?? null,
          intent: data.intent as ChatMessage['intent'],
          clarifyReason: (data.clarify_reason as string) ?? undefined,
          toolResults: (data.tool_results as ToolResultRecord[]) ?? [],
        }
      case 'progress':
        // 图内工具/检索执行中的可见状态，只展示不拼进最终正文
        return {
          ...base,
          text: base.text ?? '',
          progressLabel: (data.label as string) ?? undefined,
        }
      case 'error':
        return { ...base, error: (data.error as string) ?? '未知错误' }
      default:
        return { ...base }
    }
  }

  return provider
}

// 覆盖 DefaultChatProvider 的转换逻辑（透传 + 组装）
ragProvider.transformParams = (requestParams, _options) => ({
  query: (requestParams as Partial<AskInput>).query ?? '',
})

ragProvider.transformLocalMessage = (requestParams) => ({
  role: 'user',
  text: (requestParams as Partial<AskInput>).query ?? '',
})

ragProvider.transformMessage = (info) => {
  const { chunk } = info
  if (!chunk) {
    return { ...(info.originMessage ?? { role: 'assistant', text: '' }) }
  }
  // 后端失败（error 字段）→ 展示错误
  if (chunk.error) {
    return {
      role: 'assistant',
      text: '',
      error: chunk.error,
      materials: chunk.materials ?? [],
    }
  }
  return {
    role: 'assistant',
    text: chunk.answer ?? '',
    materials: chunk.materials ?? [],
    citations: chunk.citations ?? [],
    citationValid: !!chunk.citation_valid,
    error: undefined,
  }
}
