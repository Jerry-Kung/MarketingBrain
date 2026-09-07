import { useEffect, useRef, useState } from 'react'
import './App.css'

const API_BASE = '/api'
const UI_POLL_INTERVAL_MS = 2500

function App() {
  const [overview, setOverview] = useState(null)
  const [overviewError, setOverviewError] = useState(null)
  const [rawInput, setRawInput] = useState('')
  const [creating, setCreating] = useState(false)
  const [lastTask, setLastTask] = useState(null)
  const [tasks, setTasks] = useState([])
  const [tasksError, setTasksError] = useState(null)
  const [selectedTaskId, setSelectedTaskId] = useState(null)
  const [selectedTask, setSelectedTask] = useState(null)

  const taskIdRef = useRef(null)
  const pollTimerRef = useRef(null)

  useEffect(() => {
    fetchDataOverview()
    refreshTasks()
  }, [])

  function stopPolling() {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  async function fetchDataOverview() {
    try {
      const res = await fetch(`${API_BASE}/data-overview`)
      const data = await res.json()
      setOverview(data)
    } catch (e) {
      setOverviewError(String(e))
    }
  }

  async function refreshTasks() {
    try {
      const res = await fetch(`${API_BASE}/tasks/list`)
      const data = await res.json()
      setTasks(data.tasks || [])
      setTasksError(null)
    } catch (e) {
      setTasksError(String(e))
    }
  }

  async function handleCreateTask() {
    if (!rawInput.trim()) return
    setCreating(true)
    try {
      const res = await fetch(`${API_BASE}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_input: rawInput }),
      })
      const data = await res.json()
      setLastTask(data)
      setRawInput('')
      refreshTasks()
    } catch (e) {
      setLastTask({ error: String(e) })
    } finally {
      setCreating(false)
    }
  }

  async function refreshTask(taskId) {
    try {
      const res = await fetch(`${API_BASE}/tasks?task_id=${taskId}`)
      const task = await res.json()
      if (taskIdRef.current !== taskId) return
      setSelectedTask(task)
      if (task.status === 'success' || task.status === 'failed') {
        stopPolling()
      }
    } catch (e) {
      if (taskIdRef.current !== taskId) return
      setSelectedTask({ task_id: taskId, status: 'failed', error: String(e) })
      stopPolling()
    }
  }

  function openTask(taskId) {
    setSelectedTaskId(taskId)
    setSelectedTask(null)
    taskIdRef.current = taskId
    stopPolling()
    refreshTask(taskId)
    pollTimerRef.current = setInterval(() => refreshTask(taskId), UI_POLL_INTERVAL_MS)
  }

  function closeTask() {
    setSelectedTaskId(null)
    setSelectedTask(null)
    taskIdRef.current = null
    stopPolling()
  }

  const selectedResult = selectedTask?.result || null

  return (
    <div className="app">
      <header className="header">
        <h1>Marketing Brain</h1>
        <p className="subtitle">面向营销场景的智能策略大脑 · V0 舆情分析</p>
      </header>

      <main className="main">
        {/* 数据概览 */}
        <section className="card">
          <h2>数据概览</h2>
          {overviewError && <p className="error">数据源不可用：{overviewError}</p>}
          {!overviewError && overview && (
            <div className="stats">
              <Stat label="评论总数" value={overview.comment_count?.toLocaleString() ?? '—'} />
              <Stat label="作业数" value={overview.job_count?.toLocaleString() ?? '—'} />
              <Stat label="起始时间" value={overview.start_time ? overview.start_time.slice(0, 10) : '—'} />
              <Stat label="截止时间" value={overview.end_time ? overview.end_time.slice(0, 10) : '—'} />
            </div>
          )}
          {!overview && !overviewError && <p className="muted">加载中…</p>}
        </section>

        {/* 任务创建 */}
        <section className="card">
          <h2>发起舆情分析</h2>
          <p className="muted">用自然语言描述你的分析需求，系统将自动理解分析对象与目标。</p>
          <textarea
            className="input-area"
            placeholder="例如：分析猛士M817近期的舆情变化"
            value={rawInput}
            onChange={(e) => setRawInput(e.target.value)}
            rows={3}
          />
          <button className="primary" onClick={handleCreateTask} disabled={creating}>
            {creating ? '创建中…' : '创建任务'}
          </button>
          {lastTask && (
            <div className="result">
              {lastTask.error ? (
                <p className="error">创建失败：{lastTask.error}</p>
              ) : (
                <>
                  <p><strong>任务已创建</strong>（ID: {lastTask.task_id.slice(0, 8)}…）</p>
                  <pre>{JSON.stringify(lastTask.parsed_intent, null, 2)}</pre>
                </>
              )}
            </div>
          )}
        </section>

        {/* 任务列表 */}
        <section className="card">
          <h2>历史任务</h2>
          {tasksError && <p className="error">加载失败：{tasksError}</p>}
          {tasks.length === 0 && !tasksError && <p className="muted">暂无任务。</p>}
          <ul className="task-list">
            {tasks.map((t) => (
              <li
                key={t.task_id}
                className={`task-item${selectedTaskId === t.task_id ? ' task-item-active' : ''}`}
                onClick={() => openTask(t.task_id)}
              >
                <span className={`status status-${t.status}`}>{t.status}</span>
                <span className="task-input">{t.raw_input || '（无输入）'}</span>
                <span className="task-intent">{t.parsed_intent?.object || '待理解'}</span>
                {t.result?.meta && (
                  <span className="task-meta">
                    {t.result.meta.model} · {t.result.meta.total_tokens} tokens · {t.result.meta.latency_ms}ms
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>

        {/* 基线报告视图 */}
        {selectedTaskId && (
          <section className="card">
            <div className="report-head">
              <h2>基线报告</h2>
              <button className="primary" onClick={closeTask}>返回列表</button>
            </div>
            <ReportView task={selectedTask} result={selectedResult} />
          </section>
        )}
      </main>
    </div>
  )
}

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
    </>
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

function Stat({ label, value }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

export default App
