// 任务列表的状态筛选谓词（从 App.jsx 抽出的纯函数，便于单测覆盖）。
// 'all' 放行全部；其余取值要求 task.status 完全相等（状态缺失的任务只在 all 下出现）。
export function matchesStatus(task, statusFilter) {
  if (statusFilter === 'all') return true
  return task?.status === statusFilter
}

export default matchesStatus
