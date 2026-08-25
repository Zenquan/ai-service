/** 后端 API 类型 + 客户端（FastAPI: app.main — /api/v1 前缀） */

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init)
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
}