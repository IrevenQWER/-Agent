from __future__ import annotations

import argparse
import json
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the teacher confirmation demo workflow")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--teacher", default="T1001")
    parser.add_argument(
        "--query",
        default="生成学生 S1001 2026年3月数学学情报告，重点看成绩和作业",
    )
    args = parser.parse_args()
    headers = {"X-Teacher-ID": args.teacher}

    with httpx.Client(base_url=args.api, timeout=30) as client:
        created = client.post(
            "/api/v1/analysis/tasks",
            headers=headers,
            json={
                "query": args.query,
                "session_id": f"CLI-DEMO-{int(time.time())}",
            },
        )
        created.raise_for_status()
        task_id = created.json()["task_id"]
        print(f"任务已创建: {task_id}")

        for _ in range(50):
            response = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers)
            response.raise_for_status()
            payload = response.json()
            current_status = payload["task"]["status"]
            if current_status != "running":
                break
            time.sleep(0.2)
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if payload["task"]["status"] != "awaiting_confirmation":
            return
        report_id = payload["report"]["report_id"]
        confirmed = client.post(
            f"/api/v1/reports/{report_id}/confirmations",
            headers=headers,
            json={"action": "confirm", "comments": "CLI Demo 确认"},
        )
        confirmed.raise_for_status()
        print("确认结果:", confirmed.json())
        print(
            "Markdown 导出:",
            f"{args.api}/api/v1/reports/{report_id}/export?format=markdown",
        )


if __name__ == "__main__":
    main()
