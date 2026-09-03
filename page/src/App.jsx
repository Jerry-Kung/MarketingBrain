import { useEffect, useState } from 'react'
import './App.css'

const API_BASE = '/api'

function App() {
  const [overview, setOverview] = useState(null)
  const [overviewError, setOverviewError] = useState(null)
  const [rawInput, setRawInput] = useState('')
  const [creating, setCreating] = useState(false)
  const [lastTask, setLastTask] = useState(null)
  const [tasks, setTasks] = useState([])
  const [tasksError, setTasksError] = useState(null)

  useEffect(() => {
    fetchDataOverview()
    refreshTasks()
  }, [])

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
              <li key={t.task_id}>
                <span className={`status status-${t.status}`}>{t.status}</span>
                <span className="task-input">{t.raw_input || '（无输入）'}</span>
                <span className="task-intent">{t.parsed_intent?.object || '待理解'}</span>
              </li>
            ))}
          </ul>
        </section>
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
