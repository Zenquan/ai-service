/** 知识库面板：健康状态 + 文件上传 + 文档列表（增删） */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Badge,
  Button,
  Empty,
  List,
  Popconfirm,
  Space,
  Statistic,
  Typography,
  Upload,
} from 'antd'
import {
  DeleteOutlined,
  FileTextOutlined,
  InboxOutlined,
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

  const refresh = useCallback(async () => {
    try {
      const [h, d] = await Promise.all([api.health(), api.docs()])
      setHealth(h)
      setDocs(d)
    } catch (e) {
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
    beforeUpload: () => false, // 不自动上传，由下方手动触发
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
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 16, padding: '16px 12px', overflow: 'auto' }}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        知识库
      </Typography.Title>

      {/* 健康状态 */}
      <Space size="large" wrap>
        <Statistic title="文档数" value={health?.docs ?? '—'} />
        <Statistic title="Chunk 数" value={health?.chunks ?? '—'} />
        <Badge status={health?.rerank ? 'success' : 'default'} text={health?.rerank ? 'Rerank 开' : 'Rerank 关'} />
      </Space>
      {health && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {health.embed_model} · {health.collection}
        </Typography.Text>
      )}

      {/* 上传 */}
      <Dragger {...uploadProps} style={{ padding: 8 }}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">{uploading ? '正在入库…' : '点击或拖拽文档到此处'}</p>
        <p className="ant-upload-hint">支持 PDF / TXT / MD / DOCX / HTML，解析后自动切片入库</p>
      </Dragger>

      {/* 文档列表 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <Space style={{ marginBottom: 8 }} align="center">
          <Typography.Text strong>文档列表</Typography.Text>
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
        </Space>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {docs.length === 0 ? (
            <Empty description="暂无文档，先上传一份 PDF/MD" />
          ) : (
            <List
              size="small"
              dataSource={docs}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Popconfirm
                      key="del"
                      title={`删除《${item.doc}》？`}
                      description={`将移除 ${item.chunks} 条 chunk，不可恢复`}
                      onConfirm={() => handleDelete(item.doc)}
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      cancelText="取消"
                    >
                      <Button type="text" danger size="small" icon={<DeleteOutlined />}>
                        删除
                      </Button>
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<FileTextOutlined style={{ fontSize: 18, color: '#1677ff' }} />}
                    title={<Typography.Text ellipsis={{ tooltip: item.doc }} style={{ maxWidth: 180 }}>{item.doc}</Typography.Text>}
                    description={`${item.chunks} chunks`}
                  />
                </List.Item>
              )}
            />
          )}
        </div>
      </div>

      {!health && (
        <Alert
          type="error"
          showIcon
          message="后端不可用"
          description="请先启动 FastAPI：rag/.venv/bin/python -m uvicorn app.main:app --port 8000"
        />
      )}
    </div>
  )
}