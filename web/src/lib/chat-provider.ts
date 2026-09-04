/** 聊天消息模型 + ChatProvider（对接客服会话与兼容问答接口） */

import { DefaultChatProvider, XRequest } from '@ant-design/x-sdk'
import type { CustomerMessageResult, Material } from './api'

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
  responseMode?: 'answer' | 'clarify' | 'handoff'
  needsHuman?: boolean
  needsClarification?: boolean
  handoffReason?: string | null
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

export type CustomerMessageOutput = CustomerMessageResult

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
  const provider = new DefaultChatProvider<ChatMessage, CustomerMessageInput, CustomerMessageOutput>({
    request: XRequest<CustomerMessageInput, CustomerMessageOutput>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        manual: true,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
    const { chunk } = info
    if (!chunk) {
      return { ...(info.originMessage ?? { role: 'assistant', text: '' }) }
    }
    return {
      role: 'assistant',
      text: chunk.answer ?? '',
      materials: chunk.materials ?? [],
      citations: chunk.citations ?? [],
      citationValid: !!chunk.citation_valid,
      responseMode: chunk.response_mode,
      needsHuman: chunk.needs_human,
      needsClarification: chunk.needs_clarification,
      handoffReason: chunk.handoff_reason,
      error: chunk.error ?? undefined,
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
