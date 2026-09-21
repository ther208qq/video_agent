"""把 content_dataset 和 content_features 按 video_id 合成统计分析用的固定数据集。

不重新调 LLM，只做 join。缺一条、重一条、对不上一条都直接报错，不静默丢数据。

用法：python func/build_analysis_dataset.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "content_dataset.json"
FEATURES = ROOT / "source" / "content_features.json"
OUTPUT = ROOT / "source" / "analysis_dataset.json"

DATA_FIELDS = ("video_id", "web_url", "creator", "description", "transcript", "hashtags",
               "date_posted", "duration_ms", "play_count", "like_count", "comment_count",
               "share_count", "like_rate", "comment_rate", "share_rate")
FEATURE_FIELDS = ("has_hook", "has_question", "has_conflict", "has_reversal",
                  "emotion_score", "question_count", "topic")
RATE_FIELDS = ("like_rate", "comment_rate", "share_rate")
BOOLEAN_FIELDS = ("has_hook", "has_question", "has_conflict", "has_reversal")


def write_json(path, payload):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def merge(dataset, feature_records):
    """按 video_id join，任何对不上的情况都抛错并点名具体 video_id。"""
    if len(feature_records) != len({f["video_id"] for f in feature_records}):
        seen, dup = set(), []
        for f in feature_records:
            if f["video_id"] in seen:
                dup.append(f["video_id"])
            seen.add(f["video_id"])
        raise ValueError(f"content_features 里 video_id 重复: {sorted(set(dup))}")

    index = {f["video_id"]: f for f in feature_records}
    data_ids = [str(row["video_id"]) for row in dataset]
    if len(data_ids) != len(set(data_ids)):
        seen, dup = set(), set()
        for vid in data_ids:
            (dup if vid in seen else seen).add(vid)
        raise ValueError(f"content_dataset 里 video_id 重复: {sorted(dup)}")

    missing = [vid for vid in data_ids if vid not in index]
    if missing:
        raise ValueError(f"{len(missing)} 条视频没有 ContentFeatures: {missing}")
    extra = sorted(set(index) - set(data_ids))
    if extra:
        raise ValueError(f"{len(extra)} 条 ContentFeatures 在数据集里找不到对应视频: {extra}")

    records = []
    for row in dataset:
        vid = str(row["video_id"])
        record = {k: row[k] for k in DATA_FIELDS}
        record["video_id"] = vid
        record.update({k: index[vid][k] for k in FEATURE_FIELDS})
        records.append(record)
    return records


def check(records):
    """最小质量检查，返回问题清单（不自动修复）。"""
    problems = []
    if len(records) != 300:
        problems.append(f"count 不是 300，实际 {len(records)}")
    if len({r["video_id"] for r in records}) != len(records):
        problems.append("video_id 存在重复")
    for record in records:
        vid = record["video_id"]
        for name in BOOLEAN_FIELDS:
            if not isinstance(record.get(name), bool):
                problems.append(f"{vid} {name} 不是布尔: {record.get(name)!r}")
        if not isinstance(record.get("emotion_score"), int) or not 1 <= record["emotion_score"] <= 10:
            problems.append(f"{vid} emotion_score 非法: {record.get('emotion_score')!r}")
        if not isinstance(record.get("question_count"), int) or record["question_count"] < 0:
            problems.append(f"{vid} question_count 非法: {record.get('question_count')!r}")
        for name in RATE_FIELDS:
            if not isinstance(record.get(name), (int, float)) or isinstance(record[name], bool):
                problems.append(f"{vid} {name} 不是数值: {record.get(name)!r}")
    return problems


def main():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    features = json.loads(FEATURES.read_text(encoding="utf-8"))["features"]

    try:
        records = merge(dataset, features)
    except ValueError as e:
        print(f"join 失败: {e}", file=sys.stderr)
        return 1

    dataset_ids = {str(row["video_id"]) for row in dataset}
    feature_ids = {f["video_id"] for f in features}
    print(f"content_dataset: {len(dataset)} 条，content_features: {len(features)} 条")
    print(f"join 后: {len(records)} 条")
    print(f"join 前后 video_id 集合一致: {dataset_ids == feature_ids == {r['video_id'] for r in records}}")

    problems = check(records)
    if problems:
        print(f"质量检查未通过（{len(problems)} 项）:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    write_json(OUTPUT, {
        "source_dataset": DATASET.relative_to(ROOT).as_posix(),
        "source_features": FEATURES.relative_to(ROOT).as_posix(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(records),
        "fields": list(records[0].keys()),
        "records": records,
    })

    print(f"质量检查: 全部通过（count=300、video_id 唯一、布尔类型、emotion_score、question_count、"
          f"{'/'.join(RATE_FIELDS)} 数值）")
    print(f"字段（{len(records[0])} 个）: {', '.join(records[0].keys())}")
    print(f"已写入 {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
