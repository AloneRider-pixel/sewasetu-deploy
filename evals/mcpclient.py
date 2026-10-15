"""A real MCP client with the full OAuth 2.1 dance (discovery, DCR, PKCE, sign-in, consent), used
by the evals so they exercise the same path an assistant like Claude Code takes."""
import base64, hashlib, json, re, secrets, time
from urllib.parse import urlparse, parse_qs
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REDIRECT = "http://localhost:8765/callback"


def pkce():
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def _sms_otp(base, mobile, after_id=0, tries=15):
    for _ in range(tries):
        msgs = httpx.get(base + "/__gateway/api/messages", params={"to": mobile}).json()["messages"]
        for m in reversed(msgs):
            if m["delivered"] and m["id"] > after_id and "OTP" in (m["text"] or ""):
                return re.search(r"OTP is (\d{6})", m["text"]).group(1)
        time.sleep(1)
    raise RuntimeError("no OTP arrived")


def _last_sms_id(base, mobile):
    msgs = httpx.get(base + "/__gateway/api/messages", params={"to": mobile}).json()["messages"]
    return max([m["id"] for m in msgs] or [0])


def sign_in(base, role, mobile=None, username=None, password=None, client_name="eval-assistant"):
    """Returns dict(access_token, refresh_token, resource). Follows RFC 9728 -> RFC 8414 discovery."""
    resource = base + "/mcp/" + role
    c = httpx.Client(follow_redirects=False, timeout=20)
    r = c.post(resource, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401, "endpoint must demand a token"
    prm = c.get(re.search(r'resource_metadata="([^"]+)"', r.headers["www-authenticate"]).group(1)).json()
    asm = c.get(prm["authorization_servers"][0] + "/.well-known/oauth-authorization-server").json()
    reg = c.post(asm["registration_endpoint"], json={"client_name": client_name, "redirect_uris": [REDIRECT],
                                                      "token_endpoint_auth_method": "none"}).json()
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(8)
    q = {"client_id": reg["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": role,
         "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "resource": prm["resource"]}
    r = c.get(asm["authorization_endpoint"], params=q)
    login = base + r.headers["location"] if r.headers["location"].startswith("/") else r.headers["location"]
    if role == "citizen":
        before = _last_sms_id(base, mobile)
        c.post(login, data={"mobile": mobile})
        otp = _sms_otp(base, mobile, before)
        r = c.post(login, data={"otp": otp})
    else:
        r = c.post(login, data={"username": username, "password": password})
    consent = r.headers["location"]
    consent = base + consent if consent.startswith("/") else consent
    r = c.post(consent, data={"decision": "allow"})
    qs = parse_qs(urlparse(r.headers["location"]).query)
    assert qs["state"][0] == state
    t = c.post(asm["token_endpoint"], data={"grant_type": "authorization_code", "code": qs["code"][0],
               "client_id": reg["client_id"], "redirect_uri": REDIRECT, "code_verifier": verifier,
               "resource": prm["resource"]}).json()
    t["resource"] = resource
    t["client_id"] = reg["client_id"]
    return t


class Session:
    """with Session(url, token) as s: await s.call('tool', {...})"""
    def __init__(self, url, token):
        self.url, self.token = url, token

    async def __aenter__(self):
        self._cm = streamablehttp_client(self.url, headers={"Authorization": "Bearer " + self.token})
        r, w, _ = await self._cm.__aenter__()
        self._s = ClientSession(r, w)
        await self._s.__aenter__()
        await self._s.initialize()
        return self

    async def __aexit__(self, *a):
        await self._s.__aexit__(*a)
        await self._cm.__aexit__(*a)

    async def tools(self):
        return [t.name for t in (await self._s.list_tools()).tools]

    async def call(self, name, args=None):
        res = await self._s.call_tool(name, args or {})
        txt = "".join(getattr(c, "text", "") for c in res.content)
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw": txt, "_is_error": res.isError}
