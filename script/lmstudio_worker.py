#!/usr/bin/env python3

import json
import os
import threading
import time
import uuid
from urllib.parse import urlparse

import requests


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_base_url(base: str, default_scheme: str = "http") -> str:
    base = base.strip()
    if "://" not in base:
        base = f"{default_scheme}://{base}"
    return base.rstrip("/")


def build_tailscale_base(prefix: str, default_port: int) -> str | None:
    host = os.environ.get(f"TAILSCALE_{prefix}_HOST", "").strip()
    if not host:
        return None

    scheme = os.environ.get(f"TAILSCALE_{prefix}_SCHEME", "http").strip() or "http"
    port = int(os.environ.get(f"TAILSCALE_{prefix}_PORT", str(default_port)))
    return f"{scheme}://{host}:{port}"


def resolve_rails_base() -> str:
    explicit = os.environ.get("RAILS_API_BASE", "").strip()
    if explicit:
        return normalize_base_url(explicit)

    tailscale = build_tailscale_base("RAILS", 3000)
    if tailscale:
        return tailscale

    return "http://127.0.0.1:3000"


def resolve_lmstudio_base() -> str:
    # Keep compatibility with both LMSTUDIO_BASE_URL and LM_STUDIO_BASE_URL.
    explicit = os.environ.get("LMSTUDIO_BASE_URL", "").strip() or os.environ.get("LM_STUDIO_BASE_URL", "").strip()
    if explicit:
        return normalize_base_url(explicit)

    tailscale = build_tailscale_base("LMSTUDIO", 1234)
    if tailscale:
        return tailscale

    return "http://127.0.0.1:1234/v1"


RAILS_API_BASE = resolve_rails_base()
WORKER_SHARED_TOKEN = os.environ["WORKER_SHARED_TOKEN"]
LMSTUDIO_BASE_URL = resolve_lmstudio_base()
LMSTUDIO_MODEL = (os.environ.get("LMSTUDIO_MODEL", "").strip() or os.environ.get("LM_STUDIO_MODEL", "").strip())
LMSTUDIO_TEMPERATURE = float(os.environ.get("LMSTUDIO_TEMPERATURE", "0.2"))
LMSTUDIO_MAX_TOKENS = int(os.environ.get("LMSTUDIO_MAX_TOKENS", "1024"))
POLL_TIMEOUT_SECONDS = int(os.environ.get("POLL_TIMEOUT_SECONDS", "30"))
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "120"))
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "20"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60"))
SYSTEM_PROMPT = os.environ.get("LMSTUDIO_SYSTEM_PROMPT", "").strip()
REQUESTS_VERIFY_TLS = env_bool("REQUESTS_VERIFY_TLS", True)


session = requests.Session()
session.headers.update({"X-Worker-Token": WORKER_SHARED_TOKEN})
session.verify = REQUESTS_VERIFY_TLS


def sanitize_text(text: str) -> str:
    if not text:
        return ""

    cleaned = []
    for ch in text:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            continue
        if code < 0x20 and ch not in ("\n", "\r", "\t"):
            continue
        cleaned.append(ch)

    return "".join(cleaned)


def rails_url(path: str) -> str:
    return f"{RAILS_API_BASE}{path}"


def lmstudio_url(path: str) -> str:
    base = LMSTUDIO_BASE_URL
    parsed = urlparse(base)

    if not path.startswith("/"):
        path = f"/{path}"

    if parsed.path.rstrip("/") == "/v1":
        return f"{base}{path}"

    if path.startswith("/v1/"):
        return f"{base}{path}"

    return f"{base}/v1{path}"


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
    chunk = sanitize_text(chunk)
    if not chunk or not chunk.strip():
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

        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue

            line = raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
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
                yield sanitize_text(content)


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
    print(f"Rails API base: {RAILS_API_BASE}", flush=True)
    print(f"LM Studio base: {LMSTUDIO_BASE_URL}", flush=True)
    print(f"TLS verify: {REQUESTS_VERIFY_TLS}", flush=True)
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