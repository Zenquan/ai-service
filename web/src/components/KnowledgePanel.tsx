/** 知识库面板：模型状态 + 统计 + 上传 + 文档列表
 *
 * 布局（修复截图里的"内容被裁切"问题）：
 *  面板 = flex row 100% 高
 *    ├─ 左侧：模型信息 / 统计 / Rerank / 上传（固定宽度，不滚动）
 *    └─ 右侧：文档列表独立区块（flex:1 min-width:0 → 内部独立滚动）
 *
 * UI 细节：
 * - 统计卡片化（白底圆角），模型名胶囊展示不换行
 * - Rerank 用 Switch 展示启用状态（语义化，不再像按钮）
 * - 删除按钮 hover 才显示（减少常驻视觉噪音）
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Empty,
  List,
  Popconfirm,
  Switch,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import {
  CloudUploadOutlined,
  DeleteOutlined,
  FileTextOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { api } from '../lib/api'
import type { DocItem, Health } from '../lib/api'

const { Dragger } = Upload

export default function KnowledgePanel() {
  const { message } = AntApp.useApp()
  const [health, setHealth] = useState<Health | null>(null)
  const [docs, setDocs] = useState<DocItem[]>([])
  const [uploading, setUploading] = useState(false)
  const [loadingDocs, setLoadingDocs] = useState(false)
  // 健康状态：loading=请求中（不显示错误）；ok=已加载；error=请求失败（显示错误）
  const [healthStatus, setHealthStatus] = useState<'loading' | 'ok' | 'error'>('loading')

  const refresh = useCallback(async () => {
    try {
      const [h, d] = await Promise.all([api.health(), api.docs()])
      setHealth(h)
      setDocs(d)
      setHealthStatus('ok')
    } catch (e) {
      setHealthStatus('error')
      message.error(`读取知识库失败：${(e as Error).message}`)
    }
  }, [message])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const uploadProps: UploadProps = {
    name: 'files',
    multiple: true,
    showUploadList: false,
    disabled: uploading,
    beforeUpload: () => false,
    onChange: async (info) => {
      const files = info.fileList.map((f) => f.originFileObj as File).filter(Boolean)
      if (!files.length) return
      setUploading(true)
      try {
        const res = await api.ingest(files)
        if (res.errors?.length) {
          message.warning(`部分文件入库失败：${res.errors.map((e) => `${e.name}(${e.reason})`).join('；')}`)
        } else {
          message.success(`入库完成：${res.total} 条 chunk / ${res.docs.length} 份文档`)
        }
        await refresh()
      } catch (e) {
        message.error(`上传失败：${(e as Error).message}`)
      } finally {
        setUploading(false)
      }
    },
  }

  const handleDelete = async (docName: string) => {
    try {
      const res = await api.deleteDoc(docName)
      message.success(`已删除《${docName}》（${res.deleted} 条 chunk）`)
      await refresh()
    } catch (e) {
      message.error(`删除失败：${(e as Error).message}`)
    }
  }

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'row',
        padding: '14px 16px 18px',
        gap: 16,
        boxSizing: 'border-box',
      }}
    >
      {/* 左侧：模型状态 / 统计 / Rerank / 上传 */}
      <div
        style={{
          width: 300,
          flexShrink: 0,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {/* ① 模型状态行 */}
        <FlexRow>
          <Typography.Text strong style={{ fontSize: 15 }}>
            知识库
          </Typography.Text>
          <Tooltip title="刷新">
            <Button
              size="small"
              type="text"
              icon={<ReloadOutlined />}
              loading={loadingDocs}
              onClick={async () => {
                setLoadingDocs(true)
                await refresh()
                setLoadingDocs(false)
              }}
            />
          </Tooltip>
        </FlexRow>

        {health && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'nowrap', minWidth: 0 }}>
            <Tooltip title="Embedding 模型">
              <span
                style={{
                  fontSize: 11,
                  color: '#1677ff',
                  background: 'rgba(22,119,255,0.08)',
                  padding: '2px 8px',
                  borderRadius: 999,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: 180,
                }}
              >
                {health.embed_model}
              </span>
            </Tooltip>
            <span style={{ fontSize: 11, color: 'rgba(0,0,0,0.35)', whiteSpace: 'nowrap' }}>
              {health.docs} 文档 · {health.chunks} chunks
            </span>
          </div>
        )}

        {/* ② 统计卡片（两枚并排） */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <StatCard label="文档数" value={health?.docs ?? '—'} />
          <StatCard label="Chunk 数" value={health?.chunks ?? '—'} />
        </div>

        {/* Rerank 状态（语义化 Switch，只读展示） */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px 12px',
            background: 'rgba(0,0,0,0.02)',
            borderRadius: 8,
          }}
        >
          <div>
            <div style={{ fontSize: 13, fontWeight: 500 }}>Rerank 精排</div>
            <div style={{ fontSize: 11, color: 'rgba(0,0,0,0.4)' }}>bge-reranker-v2-m3</div>
          </div>
          <Switch checked={Boolean(health?.rerank)} size="small" disabled />
        </div>

        {/* ③ 上传区（固定不滚动） */}
        <Dragger {...uploadProps} style={{ padding: '10px 8px', borderRadius: 10 }}>
          <p className="ant-upload-drag-icon" style={{ marginBottom: 8 }}>
            <CloudUploadOutlined style={{ fontSize: 30, color: '#1677ff' }} />
          </p>
          <p className="ant-upload-text" style={{ fontSize: 13, marginBottom: 4 }}>
            {uploading ? '正在解析入库…' : '点击或拖拽文档到此处'}
          </p>
          <p className="ant-upload-hint" style={{ fontSize: 11.5 }}>
            PDF / TXT / MD / DOCX，自动切片入库
          </p>
        </Dragger>

        {healthStatus === 'error' && (
          <Alert
            type="error"
            showIcon
            message="后端不可用"
            description="请先启动 FastAPI：rag/.venv/bin/python -m uvicorn app.main:app --port 8000"
          />
        )}
      </div>

      {/* ④ 文档列表（独立滚动，修复裁切） */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          background: '#f6f8fb',
          border: '1px solid rgba(0,0,0,0.06)',
          borderRadius: 12,
          padding: '12px 12px 10px',
          boxSizing: 'border-box',
        }}
      >
        <Typography.Text strong style={{ marginBottom: 8, fontSize: 13 }}>
          文档列表{docs.length > 0 ? `（${docs.length}）` : ''}
        </Typography.Text>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 2 }}>
          {docs.length === 0 ? (
            <div style={{ paddingTop: 40 }}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无文档，先上传一份 PDF/MD" />
            </div>
          ) : (
            <List
              size="small"
              dataSource={docs}
              split={false}
              renderItem={(item) => (
                <List.Item
                  className="kb-list-row"
                  style={{
                    padding: '8px 8px',
                    borderRadius: 8,
                    background: '#fff',
                    border: '1px solid rgba(0,0,0,0.04)',
                    marginBottom: 6,
                  }}
                  actions={[
                    <Popconfirm
                      key="del"
                      title={`删除《${item.doc}》？`}
                      description={`将移除 ${item.chunks} 条 chunk，不可恢复`}
                      onConfirm={() => handleDelete(item.doc)}
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      cancelText="取消"
                      overlayStyle={{ maxWidth: 320 }}
                    >
                      <Button
                        type="text"
                        danger
                        size="small"
                        icon={<DeleteOutlined />}
                        className="kb-delete-btn"
                        style={{ color: 'rgba(0,0,0,0.35)' }}
                      >
                        删除
                      </Button>
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={
                      <FileTextOutlined style={{ fontSize: 16, color: '#1677ff', marginTop: 2 }} />
                    }
                    title={
                      <Tooltip title={item.doc}>
                        <Typography.Text
                          ellipsis={{ tooltip: null }}
                          style={{ maxWidth: 360, fontSize: 13 }}
                        >
                          {item.doc}
                        </Typography.Text>
                      </Tooltip>
                    }
                    description={
                      <span style={{ fontSize: 11, color: 'rgba(0,0,0,0.4)' }}>{item.chunks} chunks</span>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </div>
      </div>
    </div>
  )
}

/** 统计小卡片 */
function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid rgba(0,0,0,0.06)',
        borderRadius: 10,
        padding: '10px 14px',
      }}
    >
      <div style={{ fontSize: 11, color: 'rgba(0,0,0,0.45)', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.2 }}>{value}</div>
    </div>
  )
}

function FlexRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      {children}
    </div>
  )
}
