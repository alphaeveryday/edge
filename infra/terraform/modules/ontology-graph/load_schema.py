"""Upload the graph mapping to the PuppyGraph container next to it, then exit.

Runs as a non-essential sidecar in the same ECS task (infra/terraform/modules/ontology-graph). PuppyGraph
1.13 keeps its schema registry inside the container, so every start begins empty and needs this upload.
Inputs (task definition):
  GRAPH_SCHEMA_GZ      base64 gzip of libs/ontology/src/edge_ontology/sql/puppygraph-schema.json (no credentials)
  GRAPH_CREDENTIALS    JSON {username, password, db_username, db_password} from SSM SecureString
  DB_HOST              PostgreSQL host the views live in
Exit 0 after the server answers ok; exit 1 with the last error after about 10 minutes. Secrets never reach the log.
"""
import base64
import gzip
import json
import os
import time
import urllib.error
import urllib.request

URL = "http://localhost:8081/schema?postUploadBehavior=none"
ATTEMPTS, PAUSE = 60, 10


def payload(env):
    cred = json.loads(env["GRAPH_CREDENTIALS"])
    schema = json.loads(gzip.decompress(base64.b64decode(env["GRAPH_SCHEMA_GZ"])))
    schema["catalog"] = [{"name": "ontology", "type": "postgresql", "jdbc": {
        "username": cred["db_username"], "password": cred["db_password"],
        "jdbcUri": f"jdbc:postgresql://{env['DB_HOST']}:5432/edge?sslmode=require"}}]
    auth = "Basic " + base64.b64encode(f"{cred['username']}:{cred['password']}".encode()).decode()
    return json.dumps(schema).encode(), auth, [cred["password"], cred["db_password"]]


def redact(text, secrets):
    for secret in secrets:
        text = text.replace(secret, "***")
    return text[:500]


def main(env=os.environ, post=urllib.request.urlopen, sleep=time.sleep):
    body, auth, secrets = payload(env)
    request = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", "Authorization": auth})
    last = "no attempt"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with post(request, timeout=120) as response:
                answer = json.loads(response.read())
            if answer.get("ok"):
                print(json.dumps({"event": "graph_schema_uploaded", "attempt": attempt, "message": answer.get("message")}))
                return 0
            last = str(answer.get("error") or answer.get("message"))
        except urllib.error.HTTPError as error:  # server up, not ready yet ("No healthy leader") or a bad schema
            last = f"HTTP {error.code}: {error.read().decode(errors='replace')}"
        except (urllib.error.URLError, OSError) as error:  # server not listening yet
            last = f"{type(error).__name__}: {error}"
        print(json.dumps({"event": "graph_schema_waiting", "attempt": attempt, "error": redact(last, secrets)}))
        sleep(PAUSE)
    print(json.dumps({"event": "graph_schema_failed", "error": redact(last, secrets)}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
