"""报告引用校验。

从报告 JSON 中提取引用的 comment_id / job_id（video_id），逐一到本次运行的
EvidenceStore 中核对。合法引用保留；非法引用剔除并记录到 validation.rejected_refs。

设计约束：LLM 只能引用喂给它的证据；输出中出现的未知 ID 直接剔除，
不允许伪造证据混入最终报告。

统计与清理合并为同一次递归遍历（`_sanitize`）：每处引用只在其所属命名空间
（comment 或 video）核对一次，避免「清理」与「统计」各自遍历、判断口径不一致
的问题；所有比较前统一 `str()` 归一化，避免 int 类型 ID 漏计或误判。
"""


def _sanitize(obj, *, comment_ids, video_ids, kept, rejected):
    """递归清理 report 结构并同步统计校验结果，返回一份新对象（不修改入参）。

    规则（comment 与 video 两个命名空间分别核对，比较前均做 str() 归一化）：
    - 任意名为 refs 的列表：只保留 str(x) 在 comment_ids 中的元素；命中记入
      kept，未命中记入 rejected。
    - 列表中的字典元素，若带有非空 job_id：核对 str(job_id) 是否在 video_ids
      中，命中记入 kept 并保留该元素，未命中记入 rejected 并整项丢弃。
    - 字典中名为 comment_id 的字段：核对 str(value) 是否在 comment_ids 中，
      命中记入 kept 并保留该字段，未命中记入 rejected 并剔除该字段。
    - 其余字典 / 列表递归处理，标量原样返回。
    """
    if isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, dict) and item.get("job_id"):
                jid = str(item["job_id"])
                if jid not in video_ids:
                    rejected.add(jid)
                    continue
                kept.add(jid)
            if isinstance(item, (dict, list)):
                out.append(_sanitize(item, comment_ids=comment_ids, video_ids=video_ids,
                                      kept=kept, rejected=rejected))
            else:
                out.append(item)
        return out

    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if k == "refs" and isinstance(v, list):
                new_refs = []
                for cid in v:
                    cid_s = str(cid)
                    if cid_s in comment_ids:
                        kept.add(cid_s)
                        new_refs.append(cid)
                    else:
                        rejected.add(cid_s)
                new[k] = new_refs
            elif k == "comment_id":
                cid_s = str(v)
                if cid_s in comment_ids:
                    kept.add(cid_s)
                    new[k] = v
                else:
                    rejected.add(cid_s)
                    # 否则丢弃该字段（不写入 new）
            elif k == "job_id":
                # 列表元素级别的 job_id 已在上面的 list 分支核对并计数；
                # 此处仅原样透传字段值，避免重复判断。
                new[k] = v
            else:
                new[k] = _sanitize(v, comment_ids=comment_ids, video_ids=video_ids,
                                    kept=kept, rejected=rejected) \
                    if isinstance(v, (dict, list)) else v
        return new

    return obj


def validate_report(report: dict, evidence_store) -> tuple[dict, dict]:
    """校验并清理报告中的证据引用。

    Args:
        report: LLM 产出的策略报告 JSON（不会被原地修改）。
        evidence_store: 本次运行的 EvidenceStore，作为引用是否合法的唯一依据。

    Returns:
        (cleaned_report, validation)。validation 含：
        - total_refs: 报告中引用到的去重 ID 数量
        - valid_refs: 其中可在证据库回查到的去重 ID 数量
        - rejected_refs: 无法回查、已被剔除的 ID（升序去重列表）
        - rejected_count: len(rejected_refs)
        - notes: 剔除说明（无剔除时为空列表）
    """
    comment_ids = {r.comment_id for r in evidence_store.all_records() if r.comment_id}
    video_ids = {r.job_id for r in evidence_store.all_records() if r.job_id}

    kept: set[str] = set()
    rejected: set[str] = set()
    cleaned = _sanitize(report, comment_ids=comment_ids, video_ids=video_ids,
                        kept=kept, rejected=rejected)

    notes = []
    if rejected:
        notes.append(f"剔除 {len(rejected)} 条无法在证据库回查的引用")

    validation = {
        "total_refs": len(kept | rejected),
        "valid_refs": len(kept),
        "rejected_refs": sorted(rejected),
        "rejected_count": len(rejected),
        "notes": notes,
    }
    return cleaned, validation
