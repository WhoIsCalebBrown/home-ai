#!/usr/bin/env bash
set -euo pipefail

# Read-only production smoke test.  The allowlist below intentionally excludes
# every confirm/write capability, including media_standard_request.
host=${HOME_AI_HOST:-unraid}
container=${HOME_AI_ASSISTANT_CONTAINER:-Home-AI-Assistant}

ssh "$host" "docker exec '$container' python3 -c 'import urllib.request,urllib.parse,json
base=\"http://server-tools:8090\"
print(\"assistant_health=\"+urllib.request.urlopen(\"http://127.0.0.1:8088/health\",timeout=10).read().decode())
print(\"tools_health=\"+urllib.request.urlopen(base+\"/health\",timeout=10).read().decode())
for q in (\"how is Dumb and Dumber doing\", \"what happened today in American politics\", \"describe the image from that detection\"):
    url=base+\"/discover?query=\"+urllib.parse.quote(q)
    print(\"discover=\"+q+\" -> \"+urllib.request.urlopen(url,timeout=10).read().decode()[:600])
for name,args in ((\"list_containers\",{\"status\":\"running\"}), (\"media_storage_status\",{\"media_type\":\"movie\"}), (\"frigate_status\",{})):
    body={\"name\":name,\"arguments\":args,\"client_id\":\"production-smoke\",\"session_id\":\"production-smoke\"}
    req=urllib.request.Request(base+\"/invoke\",data=json.dumps(body).encode(),headers={\"Content-Type\":\"application/json\"})
    result=json.loads(urllib.request.urlopen(req,timeout=12).read().decode())
    print(\"invoke=\"+name+\" -> \"+json.dumps(result)[:1200])
    if result.get(\"status\") in (\"error\", \"failed\") or result.get(\"error\"):
        raise SystemExit(\"read-only smoke failed for \"+name+\": \"+json.dumps(result))
'"
