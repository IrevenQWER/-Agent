from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a fresh FutureEdu deployment")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    headers = {"X-Teacher-ID": "T1001"}
    deadline = time.monotonic() + args.timeout

    with httpx.Client(base_url=args.api, timeout=30) as client:
        while True:
            try:
                health = client.get("/health")
                health.raise_for_status()
                break
            except httpx.HTTPError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("API health check timed out") from None
                time.sleep(1)

        created = client.post(
            "/api/v1/analysis/tasks",
            headers=headers,
            json={
                "query": "生成学生 S1001 2026年3月数学学情报告，重点看成绩和作业",
                "session_id": "FRESH-INSTALL-VERIFY",
                "include_parent_summary": True,
            },
        )
        created.raise_for_status()
        task_id = created.json()["task_id"]

        while True:
            result = client.get(f"/api/v1/analysis/tasks/{task_id}", headers=headers)
            result.raise_for_status()
            body = result.json()
            if body["task"]["status"] != "running":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Analysis task timed out")
            time.sleep(1)

    if body["task"]["status"] != "awaiting_confirmation":
        print(body, file=sys.stderr)
        raise SystemExit(1)
    if not (body.get("validation") or {}).get("passed"):
        print(body, file=sys.stderr)
        raise SystemExit(1)
    print(
        "Fresh install verified:",
        task_id,
        body["report"]["report_id"],
        body["report"]["model_version"],
    )


if __name__ == "__main__":
    main()
