import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_json(method: str, url: str, payload=None, timeout=30):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def load_cases(path: Path):
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def wait_task(base_url: str, task_id: str, timeout_seconds: int):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        task = request_json("GET", f"{base_url}/api/task/{task_id}")
        if task.get("status") in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(2)
    raise TimeoutError(f"Task did not finish within {timeout_seconds}s: {task_id}")


def run_case(base_url: str, case: dict, timeout_seconds: int):
    session = request_json("POST", f"{base_url}/api/sessions", {})
    task = request_json(
        "POST",
        f"{base_url}/api/task",
        {
            "query": case["query"],
            "session_id": session["session_id"],
            "thread_id": session["thread_id"],
        },
    )
    finished = wait_task(base_url, task["task_id"], timeout_seconds)
    trace = {}
    try:
        trace = request_json("GET", f"{base_url}/api/eval/traces/{task['task_id']}")
    except urllib.error.HTTPError:
        pass

    result_text = str(trace.get("final_result_preview") or "")
    expected = case.get("expected_keywords", [])
    hits = [kw for kw in expected if kw.lower() in result_text.lower()]
    return {
        "case_id": case["case_id"],
        "category": case.get("category"),
        "task_id": task["task_id"],
        "status": finished.get("status"),
        "duration_ms": trace.get("duration_ms"),
        "expected_keywords": expected,
        "keyword_hits": hits,
        "hit_rate": round(len(hits) / max(len(expected), 1), 4),
        "error": finished.get("error"),
    }


def main():
    parser = argparse.ArgumentParser(description="Run Deep Search evaluation cases against the local API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--dataset", default=".jbeval/datasets/smoke.jsonl")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    cases = load_cases(Path(args.dataset))
    results = []
    for case in cases:
        print(f"Running {case['case_id']}...")
        try:
            results.append(run_case(args.base_url.rstrip("/"), case, args.timeout))
        except Exception as exc:
            results.append(
                {
                    "case_id": case["case_id"],
                    "category": case.get("category"),
                    "status": "error",
                    "hit_rate": 0.0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    avg_hit_rate = sum(row["hit_rate"] for row in results) / max(len(results), 1)
    completed = sum(1 for row in results if row.get("status") == "completed")
    summary = {
        "case_count": len(results),
        "completed": completed,
        "avg_keyword_hit_rate": round(avg_hit_rate, 4),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
