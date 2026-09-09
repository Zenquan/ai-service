import { useEffect, useState } from 'react'
import { Alert, Card, Col, Empty, Progress, Row, Spin, Statistic, Tag, Typography } from 'antd'
import { ReloadOutlined, DashboardOutlined } from '@ant-design/icons'
import { api } from '../lib/api'
import type { MetricsSummary } from '../lib/api'

const INTENT_LABEL: Record<string, string> = {
  order_query: '订单咨询',
  after_sale: '售后',
  complaint: '投诉',
  greeting: '问候',
  general: '其他',
}

const MODE_LABEL: Record<string, string> = {
  answer: '直接回答',
  clarify: '追问澄清',
  handoff: '转人工',
  manual: '人工回复',
}

const TOOL_LABEL: Record<string, string> = {
  ok: '成功',
  error: '失败',
  timeout: '超时',
  none: '未调用',
}

function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 1000) / 10 : 0
}

function fmtMs(ms: number | null): string {
  if (ms === null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`
}

/** 分布条形（label → 计数），无图表库依赖。 */
function Distribution({
  title,
  data,
  labels,
  color,
}: {
  title: string
  data: Record<string, number>
  labels: Record<string, string>
  color: string
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1])
  const max = Math.max(1, ...entries.map(([, v]) => v))
  if (entries.length === 0) {
    return (
      <div className="metrics-dist">
        <div className="metrics-dist-title">{title}</div>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />
      </div>
    )
  }
  return (
    <div className="metrics-dist">
      <div className="metrics-dist-title">{title}</div>
      {entries.map(([key, value]) => (
        <div className="metrics-dist-row" key={key}>
          <span className="metrics-dist-label">{labels[key] ?? key}</span>
          <span className="metrics-dist-bar">
            <span
              className="metrics-dist-fill"
              style={{ width: `${(value / max) * 100}%`, background: color }}
            />
          </span>
          <span className="metrics-dist-value">{value}</span>
        </div>
      ))}
    </div>
  )
}

export default function MetricsPanel() {
  const [summary, setSummary] = useState<MetricsSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    api.metricsSummary()
      .then(setSummary)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  if (loading && !summary) {
    return <div className="alerts-loading"><Spin /></div>
  }

  if (error && !summary) {
    return <Alert type="warning" showIcon message="性能数据加载失败" description={error} />
  }

  const s = summary
  if (!s) return null

  const resolvedRate = pct(s.resolved, s.total)
  const handoffRate = pct(s.handoff, s.total)
  const errorRate = pct(s.errors, s.total)
  const clarifyRate = pct(s.clarification, s.total)
  const toolCalls = s.tool_success + s.tool_failure
  const toolSuccessRate = pct(s.tool_success, toolCalls)
  const citationTotal = s.citation_valid + s.citation_invalid
  const citationValidRate = pct(s.citation_valid, citationTotal)

  return (
    <div className="metrics-panel">
      <div className="alerts-toolbar">
        <Typography.Text type="secondary">
          <DashboardOutlined style={{ marginRight: 6 }} />
          评测指标聚合（数据源：{s.storage === 'mysql' ? 'MySQL 持久化' : '进程内存'}）
        </Typography.Text>
        <a className="alerts-reload" onClick={load}><ReloadOutlined spin={loading} /> 刷新</a>
      </div>

      {error && <Alert type="warning" showIcon message="刷新失败" description={error} style={{ marginBottom: 12 }} />}

      <Row gutter={[12, 12]}>
        <Col span={6}>
          <Card size="small"><Statistic title="总请求量" value={s.total} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="一次解决率"
              value={resolvedRate}
              suffix="%"
              valueStyle={{ color: resolvedRate >= 80 ? '#2fa66a' : '#e59b2e' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="转人工率"
              value={handoffRate}
              suffix="%"
              valueStyle={{ color: handoffRate >= 50 ? '#d4380d' : '#24314d' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="出错率"
              value={errorRate}
              suffix="%"
              valueStyle={{ color: errorRate >= 30 ? '#d4380d' : '#24314d' }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col span={8}>
          <Card size="small" title="响应时延">
            <div className="metrics-latency">
              <div><span className="metrics-latency-label">平均</span><strong>{fmtMs(s.latency_avg_ms)}</strong></div>
              <div><span className="metrics-latency-label">P50</span><strong>{fmtMs(s.latency_p50_ms)}</strong></div>
              <div><span className="metrics-latency-label">P95</span><strong>{fmtMs(s.latency_p95_ms)}</strong></div>
            </div>
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="工具调用成功率">
            <div style={{ paddingTop: 8 }}>
              <Progress
                type="dashboard"
                percent={toolSuccessRate}
                size={120}
                strokeColor={toolSuccessRate >= 95 ? '#2fa66a' : '#e59b2e'}
                format={(p) => `${p}%`}
              />
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, textAlign: 'center', marginTop: 4 }}>
                成功 {s.tool_success} / 失败 {s.tool_failure}
              </Typography.Paragraph>
            </div>
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="引用校验有效率">
            <div style={{ paddingTop: 8 }}>
              <Progress
                type="dashboard"
                percent={citationValidRate}
                size={120}
                strokeColor={citationValidRate >= 90 ? '#2fa66a' : '#e59b2e'}
                format={(p) => `${p}%`}
              />
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, textAlign: 'center', marginTop: 4 }}>
                有效 {s.citation_valid} / 无效 {s.citation_invalid}
              </Typography.Paragraph>
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col span={6}>
          <Card size="small" bodyStyle={{ padding: 16 }}>
            <Statistic title="追问澄清次数" value={s.clarification} suffix={` 次（${clarifyRate}%）`} />
          </Card>
        </Col>
        <Col span={18}>
          <Card size="small" bodyStyle={{ padding: 16 }}>
            <div className="metrics-tags">
              <Tag color="blue">回答 {s.resolved} 次解决</Tag>
              <Tag color="orange">转人工 {s.handoff}</Tag>
              <Tag color="purple">追问 {s.clarification}</Tag>
              <Tag color="red">出错 {s.errors}</Tag>
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col span={8}>
          <Card size="small"><Distribution title="意图分布" data={s.by_intent} labels={INTENT_LABEL} color="#2563eb" /></Card>
        </Col>
        <Col span={8}>
          <Card size="small"><Distribution title="响应模式分布" data={s.by_response_mode} labels={MODE_LABEL} color="#2fa66a" /></Card>
        </Col>
        <Col span={8}>
          <Card size="small"><Distribution title="工具状态分布" data={s.by_tool_status} labels={TOOL_LABEL} color="#e59b2e" /></Card>
        </Col>
      </Row>
    </div>
  )
}
