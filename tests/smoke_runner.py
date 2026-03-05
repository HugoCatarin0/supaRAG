#!/usr/bin/env python3
"""
supaRAG smoke runner

Runs representative smoke scenarios against the Rust microservice proxy.
It reads tests/usage_matrix.csv and executes a curated subset that spans
all endpoint families and key transport/auth patterns.
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


@dataclass
class CaseResult:
    scenario_id: str
    endpoint: str
    method: str
    family: str
    status_code: int
    ok_transport: bool
    note: str


def load_cases(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def choose_representative_cases(cases: list[dict[str, str]]) -> list[dict[str, str]]:
    # One representative case per family + method variant, preferring minimal_valid payload.
    chosen: dict[tuple[str, str], dict[str, str]] = {}
    for row in cases:
        key = (row["family"], row["method"])
        if key in chosen:
            continue
        if row["payload_variant"] in {"none", "minimal_valid"} and row["auth_mode"] in {
            "none",
            "x-api-key",
        }:
            chosen[key] = row

    # Ensure query stream coverage
    for row in cases:
        if row["endpoint"] == "/query/stream" and row["payload_variant"] == "minimal_valid":
            chosen[("query_stream", "POST")] = row
            break

    return list(chosen.values())


def build_request(case: dict[str, str], base_url: str) -> tuple[str, dict[str, str], Any, Any]:
    endpoint = case["endpoint"].replace("{track_id}", "upload_dummy_track")
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))

    headers: dict[str, str] = {}
    auth_mode = case["auth_mode"]
    if auth_mode in {"x-api-key", "both"}:
        headers["X-API-Key"] = os.getenv("SUPARAG_TEST_API_KEY", "")
    if auth_mode in {"bearer-token", "both"}:
        token = os.getenv("SUPARAG_TEST_BEARER_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    accept = case["accept"]
    if accept != "none":
        headers["Accept"] = accept

    method = case["method"].upper()
    payload_variant = case["payload_variant"]
    endpoint_path = case["endpoint"]

    json_payload = None
    data_payload = None
    files_payload = None

    if method in {"POST", "DELETE"}:
        # Minimal payload shims for representative transport checks
        if endpoint_path == "/query":
            json_payload = {
                "query": "health check query",
                "mode": "naive",
                "top_k": 5,
            }
        elif endpoint_path == "/query/stream":
            json_payload = {
                "query": "stream health check query",
                "mode": "naive",
                "top_k": 5,
            }
        elif endpoint_path == "/documents/text":
            json_payload = {"text": "hello from supaRAG smoke"}
        elif endpoint_path == "/documents/texts":
            json_payload = {"texts": ["hello", "world"]}
        elif endpoint_path == "/documents/paginated":
            json_payload = {"page": 1, "page_size": 5, "status": "all"}
        elif endpoint_path == "/documents/clear_cache":
            json_payload = {}
        elif endpoint_path == "/documents/delete_document":
            json_payload = {
                "doc_ids": ["doc-nonexistent"],
                "delete_file": False,
                "delete_llm_cache": False,
            }
        elif endpoint_path == "/graph/entity/edit":
            json_payload = {
                "entity_name": "TEST_ENTITY",
                "updated_data": {"description": "updated by smoke"},
                "allow_rename": False,
                "allow_merge": False,
            }
        elif endpoint_path == "/graph/relation/edit":
            json_payload = {
                "source_id": "TEST_A",
                "target_id": "TEST_B",
                "updated_data": {"description": "updated by smoke"},
            }
        elif endpoint_path == "/login":
            data_payload = {
                "username": os.getenv("SUPARAG_TEST_USER", "guest"),
                "password": os.getenv("SUPARAG_TEST_PASSWORD", "guest"),
            }
        elif endpoint_path == "/documents/upload":
            sample = Path(__file__).parent / "_sample_upload.txt"
            sample.write_text("supaRAG smoke upload", encoding="utf-8")
            files_payload = {"file": (sample.name, sample.read_bytes(), "text/plain")}

    if payload_variant == "invalid_shape":
        json_payload = {"_invalid": True, "shape": 123}
    elif payload_variant == "oversized":
        json_payload = {"blob": "x" * 2_000_000}

    return url, headers, (json_payload if json_payload is not None else data_payload), files_payload


def run_case(case: dict[str, str], base_url: str, timeout: float) -> CaseResult:
    method = case["method"].upper()
    url, headers, payload, files_payload = build_request(case, base_url)
    scenario_id = case["scenario_id"]

    try:
        if files_payload is not None:
            resp = requests.request(method, url, headers=headers, files=files_payload, timeout=timeout)
        elif isinstance(payload, dict) and any(k in case["endpoint"] for k in ["/login"]):
            resp = requests.request(method, url, headers=headers, data=payload, timeout=timeout)
        elif payload is not None:
            resp = requests.request(method, url, headers=headers, json=payload, timeout=timeout)
        else:
            resp = requests.request(method, url, headers=headers, timeout=timeout)

        # Transport-level success criteria for proxy verification:
        # no 5xx from proxy itself is considered good forwarding behavior.
        ok_transport = resp.status_code < 500
        note = "ok" if ok_transport else "proxy_or_upstream_server_error"
        return CaseResult(
            scenario_id=scenario_id,
            endpoint=case["endpoint"],
            method=method,
            family=case["family"],
            status_code=resp.status_code,
            ok_transport=ok_transport,
            note=note,
        )
    except Exception as exc:
        return CaseResult(
            scenario_id=scenario_id,
            endpoint=case["endpoint"],
            method=method,
            family=case["family"],
            status_code=0,
            ok_transport=False,
            note=f"request_failed: {exc}",
        )


def main() -> None:
    root = Path(__file__).resolve().parent
    matrix_csv = root / "usage_matrix.csv"

    base_url = os.getenv("SUPARAG_BASE_URL", "http://127.0.0.1:8080")
    timeout = float(os.getenv("SUPARAG_SMOKE_TIMEOUT_SECS", "20"))

    cases = load_cases(matrix_csv)
    selected = choose_representative_cases(cases)

    results: list[CaseResult] = []
    for case in selected:
        results.append(run_case(case, base_url, timeout))
        time.sleep(0.05)

    passed = sum(1 for r in results if r.ok_transport)
    failed = len(results) - passed

    out_json = root / "smoke_results.json"
    out_md = root / "smoke_results.md"

    out_json.write_text(
        json.dumps(
            {
                "base_url": base_url,
                "selected_cases": len(results),
                "passed_transport": passed,
                "failed_transport": failed,
                "results": [r.__dict__ for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# supaRAG Smoke Results",
        "",
        f"- **base_url**: `{base_url}`",
        f"- **selected_cases**: {len(results)}",
        f"- **passed_transport**: {passed}",
        f"- **failed_transport**: {failed}",
        "",
        "| scenario_id | method | endpoint | family | status_code | ok_transport | note |",
        "|---|---|---|---|---:|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.scenario_id} | {r.method} | `{r.endpoint}` | {r.family} | {r.status_code} | {r.ok_transport} | {r.note} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "base_url": base_url,
        "selected_cases": len(results),
        "passed_transport": passed,
        "failed_transport": failed,
        "results_file": str(out_json),
        "report_file": str(out_md),
    }, indent=2))


if __name__ == "__main__":
    main()

