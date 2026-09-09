import EvidencePanel from './EvidencePanel'

// 从 App.jsx 抽离的基线报告渲染（V0.2/V0.3/V0.4/V0.6 通用）。
function ReportView({ task, result }) {
  if (!task) {
    return <p className="muted">加载中…</p>
  }

  if (task.status === 'failed') {
    return (
      <div className="report">
        <p className="error">任务未成功完成，无基线报告。</p>
        {task.error && <p className="muted">错误：{task.error}</p>}
      </div>
    )
  }

  if (task.status === 'running' || task.status === 'pending') {
    return (
      <div className="report">
        <p className="muted">分析进行中（{task.status}），系统将自动刷新…</p>
        {result?.progress && (
          <p className="muted">
            阶段：{result.progress.stage}{result.progress.tool_calls ? ` · 工具调用 ${result.progress.tool_calls} 次` : ''}
          </p>
        )}
      </div>
    )
  }

  if (!result) {
    return <p className="muted">报告生成中或失败…</p>
  }

  const meta = result.meta || null
  const validation = result.validation || null
  const report = result.report || null
  const evidence = result.evidence || []

  const evidenceByComment = new Map()
  const evidenceByJob = new Map()
  for (const ev of evidence) {
    if (ev && ev.comment_id) evidenceByComment.set(String(ev.comment_id), ev)
    if (ev && ev.job_id) evidenceByJob.set(String(ev.job_id), ev)
  }

  return (
    <>
      {meta && (
        <div className="stats">
          <Stat label="模型" value={meta.model ?? '—'} />
          <Stat label="Token" value={meta.total_tokens ?? '—'} />
          <Stat label="耗时(ms)" value={meta.latency_ms ?? '—'} />
        </div>
      )}

      {validation && (
        <div className="report-validation">
          <p className="muted">
            引用校验：通过 {validation.valid_refs ?? 0}/{validation.total_refs ?? 0}
            {Array.isArray(validation.rejected_refs) && validation.rejected_refs.length > 0
              ? `；剔除 ${validation.rejected_refs.join(', ')}`
              : ''}
          </p>
        </div>
      )}

      {report ? (
        <RenderReport report={report} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      ) : (
        <p className="muted">报告生成中或失败…</p>
      )}

      {result?.mode && <span className="report-mode-tag">模式：{result.mode}</span>}
      <EvidencePanel evidence={result?.evidence} referencedBy={buildRefMap(result?.evidence)} />
    </>
  )
}

function Stat({ label, value }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

function RenderReport({ report, evidenceByComment, evidenceByJob }) {
  return (
    <div className="report report-body">
      <ReportField label="数据覆盖" value={report.scope} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="总体声量" value={report.overall} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="主题洞察" value={report.themes} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="来源分布" value={report.sources} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="风险与机会" value={report.risk_opportunity} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="证据缺口" value={report.evidence_gaps} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="待验证假设" value={report.assumptions} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="建议行动" value={report.actions} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      <ReportField label="指标体系" value={report.metrics} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
    </div>
  )
}

function ReportField({ label, value, evidenceByComment, evidenceByJob }) {
  if (value == null || value === '') return null
  return (
    <div className="report-section">
      <h3>{label}</h3>
      <RenderValue value={value} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
    </div>
  )
}

function RenderValue({ value, evidenceByComment, evidenceByJob }) {
  if (value == null) return null
  if (typeof value === 'string' || typeof value === 'number') {
    return <p>{String(value)}</p>
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="muted">（空）</p>
    return (
      <ul className="report-list">
        {value.map((item, i) => (
          <li key={i}>
            <RenderValue value={item} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
          </li>
        ))}
      </ul>
    )
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    if (entries.length === 0) return <p className="muted">（空）</p>
    return (
      <div className="report-kv">
        {entries.map(([k, v]) => (
          <div className="report-kv-row" key={k}>
            <span className="report-kv-label">{FieldLabel(k)}</span>
            <span className="report-kv-value">
              {v == null
                ? '—'
                : typeof v === 'object'
                  ? <RenderValue value={v} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
                  : String(v)}
            </span>
          </div>
        ))}
        <ResolveRefs value={value} evidenceByComment={evidenceByComment} evidenceByJob={evidenceByJob} />
      </div>
    )
  }
  return null
}

// 报告中的某处引用（refs/comment_id/job_id）有对应证据时，展示证据文本。
function ResolveRefs({ value, evidenceByComment, evidenceByJob }) {
  const parts = []
  for (const [k, v] of Object.entries(value)) {
    if ((k === 'refs' || k === 'comment_id' || k === 'job_id') && v != null && v !== '') {
      const ids = Array.isArray(v) ? v : [v]
      for (const id of ids) {
        const idStr = String(id)
        const ev = evidenceByComment.get(idStr) || evidenceByJob.get(idStr)
        if (ev) {
          parts.push(
            <div className="report-evidence" key={`${k}-${idStr}`}>
              <span className="report-evidence-id">[{idStr}]</span>
              <span className="report-evidence-text">{ev.content || ev.video_title || ''}</span>
            </div>
          )
        }
      }
    }
  }
  if (parts.length === 0) return null
  return <div className="report-evidence-list">{parts}</div>
}

const FIELD_LABELS = {
  scope: '范围',
  overall: '总体',
  comment_count: '评论数',
  time_range: '时间窗口',
  datasource: '数据源',
  bias_note: '偏差说明',
  current: '当前',
  previous: '上期',
  change_rate: '变化率',
  themes: '主题',
  sources: '来源',
  theme: '主题',
  heat_up: '热度',
  refs: '引用',
  job_id: '作业',
  video_title: '视频',
  like_sum: '点赞合计',
  job_count: '作业数',
  type: '类型',
  title: '标题',
  reason: '理由',
  gap: '缺口',
  why: '原因',
  assumption: '假设',
  how_to_verify: '如何验证',
  action: '行动',
  priority: '优先级',
  target: '目标',
  metric: '指标',
}

function FieldLabel(k) {
  return FIELD_LABELS[k] || k
}

function buildRefMap(evidence) {
  const m = {}
  for (const ev of evidence || []) m[ev.evidence_id] = ev.referenced_by || []
  return m
}

export default ReportView
export { ReportView, buildRefMap }
