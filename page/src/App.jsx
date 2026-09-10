import { useEffect, useRef, useState } from 'react'
import './App.css'
import Timeline from './Timeline'
import AgentPlan from './AgentPlan'
import ReportView from './ReportView'
import AuditWorkbench from './AuditWorkbench'
import RawJson from './RawJson'
import { matchesStatus } from './taskFilter'

const API_BASE = '/api'
const UI_POLL_INTERVAL_MS = 2500
const STATUS_FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'running', label: '进行中' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
]

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
  const [statusFilter, setStatusFilter] = useState('all')

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
      // 瞬时错误（如网络抖动）不视为终态：保留当前状态继续轮询，下次成功取到真实状态后自动纠正
      setSelectedTask((prev) => (
        prev
          ? { ...prev, error: String(e) }
          : { task_id: taskId, status: 'running', error: String(e) }
      ))
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
          <div className="report-head">
            <h2>历史任务</h2>
            <div className="status-filter-group">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.value}
                  className={`status-filter-btn${statusFilter === f.value ? ' status-filter-btn-active' : ''}`}
                  onClick={() => setStatusFilter(f.value)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          {tasksError && <p className="error">加载失败：{tasksError}</p>}
          {tasks.length === 0 && !tasksError && <p className="muted">暂无任务。</p>}
          <ul className="task-list">
            {tasks.filter((t) => matchesStatus(t, statusFilter)).map((t) => (
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
              <span className="report-mode">
                {selectedTask?.skill_name
                  ? `Skill: ${selectedTask.skill_name}`
                  : '基线模式 (V0.2)'}
              </span>
              <button className="primary" onClick={closeTask}>返回列表</button>
            </div>
            <ReportView task={selectedTask} result={selectedResult} />
            {selectedTask && selectedTask.skill_name && (
              <Timeline taskId={selectedTask.task_id} />
            )}
            {/* 审计工作台/计划只对 Agent 任务有内容：V0.2 与 oneshot 任务没有
                subtask_* 事件，无条件渲染只会得到一个恒空的面板。 */}
            {selectedTask?.result?.agent && (
              <>
                <AgentPlan agent={selectedTask.result.agent} />
                <AuditWorkbench taskId={selectedTask.task_id} />
              </>
            )}
            <RawJson label="任务结果原始 JSON" json={selectedTask?.result} />
          </section>
        )}
      </main>
    </div>
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

export default App
