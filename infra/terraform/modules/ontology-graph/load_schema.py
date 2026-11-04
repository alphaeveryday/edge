"""Upload the graph mapping to the PuppyGraph container next to it, then exit.

Runs as a non-essential sidecar in the same ECS task (infra/terraform/modules/ontology-graph). PuppyGraph
1.13 keeps its schema registry inside the container, so every start begins empty and needs this upload.
Inputs (task definition):
  GRAPH_SCHEMA_GZ      base64 gzip of libs/ontology/src/edge_ontology/sql/puppygraph-schema.json (no credentials)
  PUPPYGRAPH_USERNAME  admin user, the same value the server container gets
  PUPPYGRAPH_PASSWORD  admin password, from the same SSM parameter as the server container
  GRAPH_CREDENTIALS    JSON with db_username and db_password (the read-only database account)
  DB_HOST              PostgreSQL host the views live in
Exit 0 once the server answers ok. Exit 1 on a 4xx answer (bad schema or credentials: retrying cannot help) or
when the server is still not ready after DEADLINE seconds. Every failure prints one graph_schema_failed line.
Secrets never reach the log.
"""
import base64
import gzip
import json
import os
import time
import urllib.error
import urllib.request

URL = "http://localhost:8081/schema?postUploadBehavior=none"
DEADLINE, PAUSE, TIMEOUT = 600, 10, 30


def payload(env):
    cred = json.loads(env["GRAPH_CREDENTIALS"])
    schema = json.loads(gzip.decompress(base64.b64decode(env["GRAPH_SCHEMA_GZ"])))
    schema["catalog"] = [{"name": "ontology", "type": "postgresql", "jdbc": {
        "username": cred["db_username"], "password": cred["db_password"],
        "jdbcUri": f"jdbc:postgresql://{env['DB_HOST']}:5432/edge?sslmode=require"}}]
    token = base64.b64encode(f"{env['PUPPYGRAPH_USERNAME']}:{env['PUPPYGRAPH_PASSWORD']}".encode()).decode()
    secrets = [env["PUPPYGRAPH_PASSWORD"], cred["db_password"], token]
    secrets += [json.dumps(s)[1:-1] for s in secrets]  # JSON-escaped forms, in case the server echoes the request
    return json.dumps(schema).encode(), "Basic " + token, [s for s in secrets if s]


def redact(text, secrets):
    for secret in secrets:
        text = text.replace(secret, "***")
    return text[:500]


def upload(env, post, sleep, clock):
    body, auth, secrets = payload(env)
    request = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", "Authorization": auth})
    start, attempt = clock(), 0
    while True:
        attempt += 1
        try:
            with post(request, timeout=TIMEOUT) as response:
                answer = json.loads(response.read())
            if isinstance(answer, dict) and answer.get("ok"):
                print(json.dumps({"event": "graph_schema_uploaded", "attempt": attempt, "message": answer.get("message")}))
                return 0
            last = redact(json.dumps(answer), secrets)
        except urllib.error.HTTPError as error:  # 500 "No healthy leader" while the backend starts; 4xx never heals
            last = redact(f"HTTP {error.code}: {error.read().decode(errors='replace')}", secrets)
            if 400 <= error.code < 500:
                raise RuntimeError(last) from None
        except (urllib.error.URLError, OSError) as error:  # server not listening yet
            last = redact(f"{type(error).__name__}: {error}", secrets)
        print(json.dumps({"event": "graph_schema_waiting", "attempt": attempt, "error": last}))
        if clock() - start >= DEADLINE:
            raise RuntimeError(f"not ready after {DEADLINE}s: {last}")
        sleep(PAUSE)


def main(env=os.environ, post=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
    try:
        return upload(env, post, sleep, clock)
    except Exception as error:  # one line the logs can be searched for, whatever went wrong
        print(json.dumps({"event": "graph_schema_failed", "error": f"{type(error).__name__}: {str(error)[:500]}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
