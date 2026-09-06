/** 后端 API 类型 + 客户端（FastAPI: app.main — /api/v1 前缀） */

import { getAuthToken } from './auth'

export interface AuthUserInfo {
  id: string
  username: string
  role: 'operator' | 'customer'
  display_name: string
}

export interface LoginResult {
  token: string
  user: AuthUserInfo
}

export interface Health {
  status: string
  docs: number
  chunks: number
  embed_model: string
  qdrant_path: string
  collection: string
  rerank: boolean
  llm_model: string
}

export interface DocItem {
  doc: string
  chunks: number
}

export interface IngestDocResult {
  name: string
  chunks: number
  inserted: number
}

export interface IngestResult {
  total: number
  docs: IngestDocResult[]
  skipped: Array<{ name: string; reason: string }>
  errors: Array<{ name: string; reason: string }>
}

export interface Material {
  text: string
  doc: string
  seq: number
  score: number
}

export interface AskResult {
  query: string
  materials: Material[]
  answer: string
  citations: number[]
  citation_valid: boolean
  material_count: number
  error: string | null
}

export interface CustomerMessageResult {
  conversation_id: string
  message_id: string
  answer: string
  materials: Material[]
  citations: number[]
  citation_valid: boolean
  response_mode: 'answer' | 'clarify' | 'handoff'
  needs_human: boolean
  needs_clarification: boolean
  handoff_reason: string | null
  error: string | null
  storage: 'mysql' | 'memory' | 'memory_fallback'
}

export interface ManualReplyResult {
  conversation_id: string
  message_id: string
  storage: 'mysql' | 'memory' | 'memory_fallback'
  response_mode: 'manual'
  agent_name: string
}

/** 业务工具统一返回（迭代 2 只读订单工具） */
export interface ToolResultRecord {
  tool: string
  status: 'ok' | 'not_found' | 'forbidden' | 'timeout' | 'error'
  ok: boolean
  message: string
  data: {
    order_no?: string
    status?: string
    carrier?: string
    tracking_no?: string
    updated_at?: string
    timeline?: Array<{ time: string; event: string }>
  } | null
  retries: number
  duration_ms: number
}

export interface ConversationSummary {
  id: string
  status: 'open' | 'handoff' | 'waiting' | 'manual'
  handoff_reason: string | null
  created_at: string
  updated_at: string
  message_count: number
  first_message: string
  storage?: string
}

export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  response_mode: 'answer' | 'clarify' | 'handoff' | 'manual' | null
  citations: number[] | null
  materials: Material[] | null
  needs_human: boolean
  handoff_reason: string | null
  created_at: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const token = getAuthToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const resp = await fetch(path, { ...init, headers })
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try {
      const body = await resp.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* 非 JSON 错误体，沿用状态文本 */
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

export const api = {
  login: (username: string, password: string) =>
    request<LoginResult>('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  me: () => request<AuthUserInfo>('/api/v1/auth/me'),

  health: () => request<Health>('/api/v1/health'),

  docs: () => request<DocItem[]>('/api/v1/docs'),

  deleteDoc: (docName: string) =>
    request<{ deleted: number }>(`/api/v1/docs/${encodeURIComponent(docName)}`, { method: 'DELETE' }),

  ingest: (files: File[]) => {
    const form = new FormData()
    for (const f of files) form.append('files', f)
    return request<IngestResult>('/api/v1/ingest', { method: 'POST', body: form })
  },

  ask: (query: string, useRerank?: boolean | null) =>
    request<AskResult>('/api/v1/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, use_rerank: useRerank ?? null }),
    }),

  customerMessage: (conversationId: string, message: string) =>
    request<CustomerMessageResult>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      },
    ),

  listConversations: () => request<ConversationSummary[]>('/api/v1/conversations'),

  conversationMessages: (conversationId: string) =>
    request<ConversationMessage[]>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
    ),

  sendManualReply: (conversationId: string, message: string, agentName = 'Zenquan') =>
    request<ManualReplyResult>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/manual`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, agent_name: agentName }),
      },
    ),
}
