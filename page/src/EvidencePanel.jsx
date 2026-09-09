// 证据反向：点击证据查看被哪些判断引用（referenced_by）。
export default function EvidencePanel({ evidence, referencedBy }) {
  if (!evidence || evidence.length === 0) return null
  return (
    <div className="evidence-panel">
      <h3>证据</h3>
      <ul className="evidence-list">
        {evidence.map((ev) => {
          const refs = referencedBy[ev.evidence_id] || []
          return (
            <li key={ev.evidence_id} className="evidence-item">
              <span className="evidence-kind">{ev.kind}</span>
              <span className="evidence-content">{ev.content || ev.video_title || ev.extra?.title || ev.extra?.label || ''}</span>
              {refs.length > 0 && (
                <span className="evidence-refs">被 {refs.length} 处引用</span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
