#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

URL = "https://opencode.ai/inference/openai/v1/chat/completions"
MODEL = "deepseek-v4-flash-free"

payload = {
    "model": MODEL,
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
    headers={"Content-Type": "application/json", "User-Agent": "validated-agent-bootstrap/0.1"},
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read().decode()
except urllib.error.HTTPError as exc:
    body = exc.read().decode(errors="replace")
    print(f"HTTP_ERROR status={exc.code} body={body[:1200]}", file=sys.stderr)
    raise SystemExit(2)
except Exception as exc:
    print(f"REQUEST_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(3)

try:
    obj = json.loads(raw)
    text = obj["choices"][0]["message"]["content"].strip()
except Exception as exc:
    print(f"PARSE_ERROR {type(exc).__name__}: {exc}; raw={raw[:1500]}", file=sys.stderr)
    raise SystemExit(4)

print(f"model={MODEL}")
print(f"response={text}")
if text != "FREE_MODEL_OK":
    print("SEMANTIC_ERROR expected exact FREE_MODEL_OK", file=sys.stderr)
    raise SystemExit(5)
print("SMOKE_PASS")
