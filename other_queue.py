#!/usr/bin/env python3
"""GitHub Actions OIDC client for the independent Other queue."""
from __future__ import annotations
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
QUEUE_URL = os.environ.get("OTHER_QUEUE_URL", "https://fjxhvexxlhvjagbbutth.supabase.co/functions/v1/other-queue")
AUDIENCE = "other-supabase-queue"
def request_json(url: str, headers: dict[str, str], payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw, status = res.read(), res.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from None
    if status < 200 or status >= 300: raise RuntimeError(f"HTTP {status}")
    try: value = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError: raise RuntimeError("invalid JSON response") from None
    if not isinstance(value, dict): raise RuntimeError("invalid response")
    return value
def oidc_token() -> str:
    url, bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer: raise RuntimeError("GitHub OIDC environment is unavailable")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}{urllib.parse.urlencode({'audience': AUDIENCE})}", headers={"Authorization": f"bearer {bearer}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res: value = json.loads(res.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError): raise RuntimeError("OIDC token request failed") from None
    token = value.get("value") if isinstance(value, dict) else None
    if not isinstance(token, str) or not token: raise RuntimeError("OIDC token was not returned")
    return token
def call(action: str, **payload: object) -> dict:
    return request_json(QUEUE_URL, {"content-type":"application/json", "x-github-oidc-token":oidc_token()}, {"action":action, **payload})
def output(name: str, value: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f: f.write(f"{name}={value}\n")
def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value: raise RuntimeError(f"missing {name}")
    return value
def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "enqueue":
        result = call("enqueue", task_type=required("OTHER_TASK_TYPE"), link=os.environ.get("TARGET_LINK", "").strip())
        if not result.get("queue_id"): raise RuntimeError("queue id was not returned")
        print("manual request queued"); return 0
    if command == "claim":
        result = call("claim", queue_id=required("OTHER_QUEUE_ID"), fencing_token=required("OTHER_FENCING_TOKEN"), task_type=required("OTHER_TASK_TYPE"), link=os.environ.get("TARGET_LINK", "").strip())
        process = result.get("process") is True
        output("process", str(process).lower())
        print("queue claim accepted" if process else "queue claim declined"); return 0
    if command == "heartbeat":
        while True:
            result = call("heartbeat", queue_id=required("OTHER_QUEUE_ID"), fencing_token=required("OTHER_FENCING_TOKEN"))
            if result.get("accepted") is not True: raise RuntimeError("heartbeat was not accepted")
            time.sleep(30)
    if command == "complete":
        result = call("complete", queue_id=required("OTHER_QUEUE_ID"), fencing_token=required("OTHER_FENCING_TOKEN"), success=os.environ.get("OTHER_SUCCESS", "false").lower() == "true")
        if result.get("accepted") is not True: raise RuntimeError("completion was not accepted")
        print("queue completion accepted"); return 0
    raise RuntimeError("usage: other_queue.py {enqueue|claim|heartbeat|complete}")
if __name__ == "__main__":
    try: raise SystemExit(main())
    except RuntimeError as exc:
        print(f"queue helper error: {exc}", file=sys.stderr); raise SystemExit(1)
