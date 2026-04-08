#!/usr/bin/env python3

import json
import os
import threading
import time
import uuid

import requests


RAILS_API_BASE = os.environ.get("RAILS_API_BASE", "http://127.0.0.1:3000").rstrip("/")
WORKER_SHARED_TOKEN = os.environ["WORKER_SHARED_TOKEN"]
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://100.126.42.42:1234/v1").rstrip("/")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "").strip()
LMSTUDIO_TEMPERATURE = float(os.environ.get("LMSTUDIO_TEMPERATURE", "0.2"))
LMSTUDIO_MAX_TOKENS = int(os.environ.get("LMSTUDIO_MAX_TOKENS", "1024"))
POLL_TIMEOUT_SECONDS = int(os.environ.get("POLL_TIMEOUT_SECONDS", "30"))
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "120"))
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "20"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60"))
SYSTEM_PROMPT = os.environ.get("LMSTUDIO_SYSTEM_PROMPT", "").strip()


session = requests.Session()
session.headers.update({"X-Worker-Token": WORKER_SHARED_TOKEN})


def rails_url(path: str) -> str:
    return f"{RAILS_API_BASE}{path}"


def lmstudio_url(path: str) -> str:
    return f"{LMSTUDIO_BASE_URL}{path}"


def discover_model() -> str:
    if LMSTUDIO_MODEL:
        return LMSTUDIO_MODEL

    response = session.get(lmstudio_url("/models"), timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()

    data = payload.get("data", [])
    if not data:
        raise RuntimeError("LM Studio did not return any models")

    model_id = data[0].get("id")
    if not model_id:
        raise RuntimeError("LM Studio returned a model without an id")

    return model_id


def claim_job() -> dict | None:
    response = session.post(
        rails_url("/api/jobs/claim"),
        json={"timeout_seconds": POLL_TIMEOUT_SECONDS, "lease_seconds": LEASE_SECONDS},
        timeout=POLL_TIMEOUT_SECONDS + 5,
    )

    if response.status_code == 204:
        return None

    response.raise_for_status()
    return response.json()


def send_heartbeat(job_id: int, lease_token: str) -> None:
    session.post(
        rails_url(f"/api/jobs/{job_id}/heartbeat"),
        json={"lease_token": lease_token, "lease_seconds": LEASE_SECONDS},
        timeout=REQUEST_TIMEOUT_SECONDS,
    ).raise_for_status()


def send_chunk(job_id: int, lease_token: str, chunk: str) -> None:
    if not chunk:
        return

    session.post(
        rails_url(f"/api/jobs/{job_id}/chunk"),
        json={"lease_token": lease_token, "chunk": chunk},
        timeout=REQUEST_TIMEOUT_SECONDS,
    ).raise_for_status()


def send_result(job_id: int, lease_token: str, success: bool, response_text: str = "", error: str = "") -> None:
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    payload = {"lease_token": lease_token, "success": success}

    if success:
        payload["response"] = response_text
    else:
        payload["error"] = error or "worker_failed"

    session.post(
        rails_url(f"/api/jobs/{job_id}/result"),
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ).raise_for_status()


def stream_completion(model: str, prompt_text: str):
    messages = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": prompt_text})

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": LMSTUDIO_TEMPERATURE,
        "max_tokens": LMSTUDIO_MAX_TOKENS,
    }

    with session.post(
        lmstudio_url("/chat/completions"),
        json=payload,
        stream=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()

        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue

            data = line[6:].strip()
            if data == "[DONE]":
                break

            chunk = json.loads(data)
            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                yield content


def process_job(job: dict, model: str) -> None:
    job_id = job["id"]
    lease_token = job["lease_token"]
    prompt_text = job["prompt"]

    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
            send_heartbeat(job_id, lease_token)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    response_parts = []

    try:
        for chunk in stream_completion(model, prompt_text):
            response_parts.append(chunk)
            send_chunk(job_id, lease_token, chunk)

        send_result(job_id, lease_token, True, "".join(response_parts))
    except Exception as exc:
        try:
            send_result(job_id, lease_token, False, error=str(exc))
        except Exception:
            pass
        raise
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


def main() -> None:
    model = discover_model()
    print(f"Using LM Studio model: {model}", flush=True)

    while True:
        try:
            job = claim_job()
            if job is None:
                continue

            print(f"Claimed job {job['id']}", flush=True)
            process_job(job, model)
            print(f"Completed job {job['id']}", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Worker error: {exc}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()