"""/eval-dashboard: the invented people, what they did through the MCP endpoints, what was checked."""
import json, os, html
from starlette.routing import Route
from starlette.responses import HTMLResponse, JSONResponse

EV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals")


def _load(name):
    try:
        return json.load(open(os.path.join(EV, name)))
    except Exception:
        return None


E_ = html.escape


def render():
    run = _load("last_run.json")
    finds = _load("findings.json") or []
    if not run:
        return "<h1>Eval dashboard</h1><p>No run recorded yet.</p>"
    res = run["results"]
    tot = sum(len(r["checks"]) for r in res); ok = sum(c["pass"] for r in res for c in r["checks"])
    h = ["""<!doctype html><html><head><meta charset="utf-8"><title>Sewa Setu assistant - eval dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{font-family:system-ui,Arial,sans-serif;max-width:1000px;margin:20px auto;padding:0 14px;color:#222;background:#fafafa}
h1{color:#1a3c6e}.card{background:#fff;border:1px solid #ccd;border-radius:8px;margin:14px 0;padding:12px 16px}
.pass{color:#1b7e3c;font-weight:700}.fail{color:#b00020;font-weight:700}.pill{font-size:12px;border-radius:10px;padding:1px 8px;background:#e8eefc;margin-left:6px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #eee;padding:4px 6px;text-align:left;vertical-align:top}
code,pre{background:#f3f3f3;font-size:12px;white-space:pre-wrap;word-break:break-word}details{margin:6px 0}small{color:#555}
</style></head><body>"""]
    h.append("<h1>Sewa Setu assistant: evals</h1>")
    h.append("<p><b>%d of %d checks passed</b> across <b>%d invented people</b> (%d fully passing). Run %s against <code>%s</code>.</p>" %
             (ok, tot, len(res), sum(r["passed"] for r in res), E_(run["at"]), E_(run["base"])))
    h.append("<p><small>The harness needs direct database access for its setup and checks, which a hosted free instance does not expose. So the full run was made against a from-scratch boot of this same build (same Docker image, same seed). The hosted instance was smoke-tested separately over the public URL: OAuth sign-in and the citizen MCP tool list work.</small></p>")
    h.append("<p><small>How this was run: each person is played by a script that calls the real citizen or officer MCP endpoint over streamable HTTP "
             "after a real OAuth 2.1 sign-in (discovery, dynamic registration, PKCE, OTP or officer login, consent). Afterwards the harness reads the "
             "portal database and the portal's own web pages. There is no language model in this loop: it tests what the servers enforce, "
             "not how a particular model phrases things. Test data uses mobiles 90000001xx and is cleaned and recreated on every run.</small></p>")
    h.append("<div class='card'><b>Summary</b><table><tr><th>Person</th><th>Role</th><th>Checks</th><th>Result</th></tr>")
    for r in res:
        n = sum(c["pass"] for c in r["checks"])
        h.append("<tr><td><a href='#%s'>%s</a></td><td>%s</td><td>%d/%d</td><td class='%s'>%s</td></tr>" % (
            E_(r["persona"]), E_(r["name"]), E_(r["role"]), n, len(r["checks"]), "pass" if r["passed"] else "fail", "PASS" if r["passed"] else "FAIL"))
    h.append("</table></div>")
    h.append("<h2>What the evals found, and what I changed</h2>")
    for f in finds:
        h.append("<div class='card'><b>Found:</b> %s<br><b>Changed:</b> %s<br><small>%s</small></div>" % (E_(f["found"]), E_(f["changed"]), E_(f["where"])))
    h.append("<h2>The people</h2>")
    for r in res:
        p = r["profile"]
        h.append("<div class='card' id='%s'><h3>%s <span class='pill'>%s</span> <span class='%s'>%s</span></h3>" % (
            E_(r["persona"]), E_(r["name"]), E_(r["role"]), "pass" if r["passed"] else "fail", "PASS" if r["passed"] else "FAIL"))
        if p:
            h.append("<p><b>Who:</b> %s%s%s<br><b>How they talk:</b> %s<br><b>Wants:</b> %s<br><b>Why this person:</b> %s<br><b>Must be true at the end:</b> %s</p>" % (
                ("age %s, " % p["age"]) if p.get("age") else "", E_(str(p.get("village", ""))), (", " + E_(p["block"]) + " block") if p.get("block") not in (None, "-") else "",
                E_(p.get("talks", "")), E_(p.get("goal", "")), E_(p.get("why", "")), E_(p.get("expect", ""))))
        h.append("<table><tr><th></th><th>Check (read from the database / web pages / endpoint)</th><th>Observed</th></tr>")
        for c in r["checks"]:
            h.append("<tr><td class='%s'>%s</td><td>%s</td><td><small>%s</small></td></tr>" % ("pass" if c["pass"] else "fail", "pass" if c["pass"] else "FAIL", E_(c["check"]), E_(c["detail"])))
        h.append("</table><details><summary>Conversation with the endpoint (%d steps)</summary><table>" % len(r["transcript"]))
        for t in r["transcript"]:
            if "note" in t:
                h.append("<tr><td colspan=3><i>%s</i></td></tr>" % E_(t["note"]))
            else:
                h.append("<tr><td><code>%s</code></td><td><code>%s</code></td><td><code>%s</code></td></tr>" % (E_(t["tool"]), E_(json.dumps(t["args"], ensure_ascii=False)), E_(t["result"])))
        h.append("</table></details></div>")
    h.append("</body></html>")
    return "".join(h)


def dashboard_routes():
    async def page(request):
        return HTMLResponse(render())

    async def raw(request):
        return JSONResponse(_load("last_run.json") or {})
    return [Route("/eval-dashboard", page), Route("/eval-dashboard/", page), Route("/eval-dashboard.json", raw)]
