#!/usr/bin/env python3
"""Sign and POST a deploy request to deployd, then poll until it finishes.

Stdlib only — vendor this file into your app repo and call it from CI.
Env: DEPLOYD_URL (e.g. https://deploy.example.com), DEPLOYD_SECRET.
Exit code: 0 deployed, 1 deploy failed, 2 bad invocation.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

TERMINAL = {"succeeded", "failed", "rolled_back"}


class DeploydHTTPError(RuntimeError):
    pass


def sign(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + nonce.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def request(url: str, *, body: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except json.JSONDecodeError as exc:
        raise RuntimeError("deployd returned invalid JSON") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise DeploydHTTPError(f"deployd returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"deployd request failed: {exc.__class__.__name__}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(prog="notify_deploy")
    parser.add_argument("--app", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--triggered-by", default=f"ci:{os.environ.get('GITHUB_RUN_ID', 'manual')}")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--poll-interval", type=int, default=3)
    args = parser.parse_args()

    base = os.environ.get("DEPLOYD_URL", "").rstrip("/")
    secret = os.environ.get("DEPLOYD_SECRET", "")
    if not base or not secret:
        print("DEPLOYD_URL and DEPLOYD_SECRET must be set", file=sys.stderr)
        return 2
    if len(secret.encode()) < 32:
        print("DEPLOYD_SECRET must contain at least 32 bytes", file=sys.stderr)
        return 2

    body = json.dumps(
        {
            "app": args.app,
            "commit_sha": args.commit_sha,
            "artifact_url": args.artifact_url,
            "artifact_sha256": args.artifact_sha256,
            "triggered_by": args.triggered_by,
        }
    ).encode()
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Deploy-Timestamp": timestamp,
        "X-Deploy-Nonce": nonce,
        "X-Deploy-Signature": "sha256=" + sign(secret, timestamp, nonce, body),
    }

    accepted = None
    for attempt in range(3):
        try:
            # Identical signed retries are idempotent and return the original ID.
            accepted = request(f"{base}/deploys", body=body, headers=headers)
            break
        except DeploydHTTPError as exc:
            raise SystemExit(str(exc)) from exc
        except RuntimeError as exc:
            if attempt == 2:
                raise SystemExit(str(exc)) from exc
            time.sleep(2**attempt)
    deploy_id = accepted["deploy_id"]
    print(f"deploy {deploy_id} accepted, polling...")

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            status = request(f"{base}/deploys/{deploy_id}")
        except DeploydHTTPError as exc:
            raise SystemExit(str(exc)) from exc
        except RuntimeError as exc:
            print(f"poll retry: {exc}", file=sys.stderr)
            time.sleep(args.poll_interval)
            continue
        if status["status"] in TERMINAL:
            for step in status["steps"]:
                line = f"  {step['step']}: {step['status']}"
                if step.get("output"):
                    line += f" — {step['output']}"
                print(line)
            print(f"deploy {deploy_id}: {status['status']}")
            return 0 if status["status"] == "succeeded" else 1
        time.sleep(args.poll_interval)

    print(f"deploy {deploy_id}: still running after {args.timeout}s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
