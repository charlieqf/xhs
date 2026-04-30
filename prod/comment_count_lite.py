"""按日统计 comment_responses*.json 回复记录，输出 date,count 两列表格。"""

import argparse
import glob
import io
import json
import os
import sys
from collections import Counter

# 强制标准输出使用 UTF-8，避免 Windows 控制台 GBK 编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMMENT_RESPONSES_DIR = os.path.join(SCRIPT_DIR, "comment_responses")


def get_record_key(record: dict) -> tuple:
    """生成回复记录去重键，避免累计快照文件重复计数。"""
    return (
        record.get("profile", ""),
        record.get("note_id", ""),
        record.get("target_comment_id", ""),
        record.get("timestamp", ""),
        record.get("generated_reply", ""),
        record.get("send_status", ""),
    )


def parse_date(timestamp: str) -> str:
    """从时间戳中提取日期（YYYY-MM-DD）。"""
    if not timestamp:
        return "unknown"
    try:
        return timestamp.split(" ")[0]
    except Exception:
        return "unknown"


def load_all_responses(directory: str) -> list[dict]:
    """加载目录下所有 comment_responses*.json 文件。"""
    records: list[dict] = []
    seen_records = set()
    pattern = os.path.join(directory, "comment_responses*.json")

    for path in sorted(glob.glob(pattern)):
        # 跳过子目录中的文件
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(directory):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, list):
            continue
        for record in data:
            if not isinstance(record, dict):
                continue
            key = get_record_key(record)
            if key in seen_records:
                continue
            seen_records.add(key)
            records.append(record)

    return records


def compute_daily_counts(records: list[dict]) -> Counter:
    """按日期统计回复记录数。"""
    counter: Counter = Counter()
    for record in records:
        counter[parse_date(record.get("timestamp", ""))] += 1
    return counter


def main() -> None:
    parser = argparse.ArgumentParser(description="输出每日评论回复数量：date,count")
    parser.add_argument(
        "--directory",
        default=COMMENT_RESPONSES_DIR,
        help="comment_responses*.json 所在目录",
    )
    args = parser.parse_args()

    daily_counts = compute_daily_counts(load_all_responses(args.directory))

    print("date,count")
    for date in sorted(daily_counts):
        print(f"{date},{daily_counts[date]}")
    print(f"summary,{sum(daily_counts.values())}")


if __name__ == "__main__":
    main()
