"""Shared plumbing for the two MCP servers: portal access, token verification, drafts."""
import os
import sys
import json
import time
import hashlib
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import app as portal          # the Flask portal: rules, DB, audit
import oauth as portal_oauth  # the portal's OAuth server: keys, sessions

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.middleware.auth_context import get_access_token

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
CITIZEN_URL = PUBLIC_BASE_URL + "/mcp/citizen"
OFFICER_URL = PUBLIC_BASE_URL + "/mcp/officer"

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_drafts (
    mobile     VARCHAR(15) PRIMARY KEY,
    data       JSONB,
    doc_name   VARCHAR(200),
    doc_path   VARCHAR(300),
    client     VARCHAR(100),
    updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_eval_runs (
    id        SERIAL PRIMARY KEY,
    run_id    VARCHAR(40),
    persona   VARCHAR(80),
    role      VARCHAR(10),
    profile   JSONB,
    transcript JSONB,
    checks    JSONB,
    passed    BOOLEAN,
    note      TEXT,
    at        TIMESTAMP
);
"""


def ensure_schema():
    conn = portal.get_db()
    cur = conn.cursor()
    cur.execute(SCHEMA)
    conn.commit()
    cur.close(); conn.close()


def db():
    return portal.get_db()


def has_live_session(subject, audience):
    """A person can end a sign-in (status portal, officer dashboard, end_session). Access tokens
    are short JWTs, so check the sign-in behind them is still open, to honour that at once."""
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM oauth_refresh_tokens WHERE subject = %s AND resource = %s "
                "AND revoked_at IS NULL AND expires_at > %s LIMIT 1",
                (subject, audience, datetime.now()))
    ok = cur.fetchone() is not None
    cur.close(); conn.close()
    return ok


def program_token_live(jti):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM oauth_program_tokens WHERE jti = %s AND revoked_at IS NULL "
                "AND expires_at > %s", (jti, datetime.now()))
    ok = cur.fetchone() is not None
    cur.close(); conn.close()
    return ok


class PortalTokenVerifier(TokenVerifier):
    """Accept only tokens this portal issued, for this server's own URL, for this role."""

    def __init__(self, audience, role):
        self.audience, self.role = audience, role

    async def verify_token(self, token):
        try:
            r = self._verify(token)
            import logging; logging.getLogger('agents').warning('verify -> %s', bool(r))
            return r
        except Exception as e:
            import logging; logging.getLogger('agents').exception('token verification failed: %s', e)
            return None

    def _verify(self, token):
        claims = portal_oauth.verify_token(token, self.audience)   # signature, exp, iss, aud
        if not claims or claims.get("role") != self.role or claims.get("scope") != self.role:
            return None
        if claims.get("kind") == "program":
            if not program_token_live(claims["jti"]):
                return None
        elif not has_live_session(claims["sub"], self.audience):
            return None
        return AccessToken(token=token, client_id=claims.get("client_id", ""),
                           scopes=[claims["scope"]], expires_at=claims["exp"],
                           resource=claims["aud"])


def current_claims():
    at = get_access_token()
    if not at:
        raise PermissionError("not signed in")
    c = jwt.decode(at.token, options={"verify_signature": False})
    return c


def client_label(claims):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT client_name FROM oauth_clients WHERE client_id = %s", (claims.get("client_id"),))
    r = cur.fetchone()
    cur.close(); conn.close()
    return (r[0] if r else claims.get("client_id", "unknown"))[:60]


def mask_account(acc):
    acc = acc or ""
    return ("*" * max(len(acc) - 4, 0)) + acc[-4:]


def mask_mobile(m):
    m = m or ""
    return m[:2] + "******" + m[-2:] if len(m) == 10 else m


def age_years(dob, on):
    return portal.age_on(dob, on)
