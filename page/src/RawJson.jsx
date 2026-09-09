import { useState } from 'react'

// 原始 JSON 折叠查看：用于失败任务定位与结果审计。
export default function RawJson({ label = '原始 JSON', json }) {
  const [open, setOpen] = useState(false)
  if (json == null) return null
  return (
    <div className="raw-json">
      <button className="raw-json-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? '收起' : '展开'} {label}
      </button>
      {open && <pre className="raw-json-pre">{JSON.stringify(json, null, 2)}</pre>}
    </div>
  )
}
