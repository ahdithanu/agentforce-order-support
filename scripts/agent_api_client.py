"""Headless client for the Acme Order Support agent via the Agentforce Agent API.

Talks to the agent the way a customer's own app or backend would: OAuth client credentials,
then start a session, send messages, end the session. Standard library only.

Credentials come from the environment and are never printed:
  ACME_SF_INSTANCE_URL   e.g. https://your-domain.my.salesforce.com
  ACME_AGENT_CLIENT_ID   External Client App consumer key
  ACME_AGENT_CLIENT_SECRET
  ACME_AGENT_ID          BotDefinition Id (0Xx...) of the active agent

Usage:
  python3 scripts/agent_api_client.py                      # interactive chat
  python3 scripts/agent_api_client.py "Where is my order?" "jamie@example.com, A-1001"
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_BASE = "https://api.salesforce.com/einstein/ai-agent/v1"


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing environment variable {name}. See the header of this file.")
    return value


def call(method, url, token=None, body=None, form=None, headers=None, retries=3):
    hdrs = dict(headers or {})
    data = None
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:300]
            if err.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            # Never echo request headers or bodies: they can carry the token or secret.
            sys.exit(f"{method} {url.split('?')[0]} failed: HTTP {err.code} {detail}")


def get_token(instance_url):
    result = call("POST", f"{instance_url}/services/oauth2/token", form={
        "grant_type": "client_credentials",
        "client_id": env("ACME_AGENT_CLIENT_ID"),
        "client_secret": env("ACME_AGENT_CLIENT_SECRET"),
    })
    return result["access_token"]


class AgentSession:
    def __init__(self):
        self.instance_url = env("ACME_SF_INSTANCE_URL").rstrip("/")
        self.agent_id = env("ACME_AGENT_ID")
        self.token = get_token(self.instance_url)
        self.sequence = 0
        started = call("POST", f"{API_BASE}/agents/{self.agent_id}/sessions", token=self.token, body={
            "externalSessionKey": str(uuid.uuid4()),
            "instanceConfig": {"endpoint": self.instance_url},
            "tz": "America/Los_Angeles",
            "variables": [{"name": "$Context.EndUserLanguage", "type": "Text", "value": "en_US"}],
            "featureSupport": "Sync",
            "bypassUser": True,
        })
        self.session_id = started["sessionId"]
        self.greeting = [m.get("message") for m in started.get("messages", []) if m.get("message")]

    def send(self, text):
        self.sequence += 1
        started = time.monotonic()
        result = call("POST", f"{API_BASE}/sessions/{self.session_id}/messages", token=self.token, body={
            "message": {"sequenceId": self.sequence, "type": "Text", "text": text},
        })
        elapsed_ms = int((time.monotonic() - started) * 1000)
        replies = []
        for m in result.get("messages", []):
            kind = m.get("type")
            if kind == "Escalate":
                replies.append("[handoff to a human agent requested]")
            elif m.get("message"):
                replies.append(m["message"])
        return replies, elapsed_ms

    def end(self):
        call("DELETE", f"{API_BASE}/sessions/{self.session_id}", token=self.token,
             headers={"x-session-end-reason": "UserRequest"})


def main():
    session = AgentSession()
    print(f"Session started ({session.session_id[:8]}...).")
    for line in session.greeting:
        print(f"agent> {line}")
    scripted = sys.argv[1:]
    try:
        inputs = iter(scripted) if scripted else iter(lambda: input("you> "), None)
        for text in inputs:
            if not text or text.strip().lower() in ("quit", "exit"):
                break
            if scripted:
                print(f"you> {text}")
            replies, ms = session.send(text)
            for reply in replies:
                print(f"agent> {reply}")
            print(f"       ({ms} ms)")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        session.end()
        print("Session ended.")


if __name__ == "__main__":
    main()
