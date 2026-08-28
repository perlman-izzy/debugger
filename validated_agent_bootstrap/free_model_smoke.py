#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

URL = "https://opencode.ai/inference/openai/v1/chat/completions"
MODELS = [
    "hy3-free",
    "mimo-v2.5-free",
    "nemotron-3.5-lightning-free",
    "nemotron-3-ultra-free",
]


def call(model: str) -> tuple[bool, str]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a connectivity smoke test. Follow the user instruction exactly and add nothing else."},
            {"role": "user", "content": "Reply exactly FREE_MODEL_OK"},
        ],
        "max_tokens": 32,
        "temperature": 0,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "validated-agent-bootstrap/0.2"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return False, f"HTTP {exc.code}: {body[:500]}"
    except Exception as exc:
        return False, f"REQUEST {type(exc).__name__}: {exc}"

    try:
        obj = json.loads(raw)
        text = obj["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return False, f"PARSE {type(exc).__name__}: {exc}; raw={raw[:700]}"

    if text != "FREE_MODEL_OK":
        return False, f"SEMANTIC response={text!r}"
    return True, text


failures: list[str] = []
for model in MODELS:
    ok, detail = call(model)
    print(f"probe model={model} ok={ok} detail={detail}")
    if ok:
        print(f"selected_model={model}")
        print("SMOKE_PASS")
        raise SystemExit(0)
    failures.append(f"{model}: {detail}")

print("ALL_FREE_MODEL_PROBES_FAILED", file=sys.stderr)
for failure in failures:
    print(failure, file=sys.stderr)
raise SystemExit(2)
