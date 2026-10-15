"""Citizen MCP server: lets an AI assistant help one signed-in citizen apply for the old age
pension and follow it up. Same rules as the portal (portal.validate_application /
create_application), no officer tools, one citizen per sign-in (the mobile in the token)."""
import re
import base64
import hashlib
import json
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from . import common as C
from .common import portal

INSTRUCTIONS = """You help ONE citizen (the person who signed in) with the Sewa Setu old age
pension (Rs 250/month, residents aged 60+). Be patient and plain: many applicants are old and
share phones. Rules you must follow:
- Never invent or guess any detail. Ask the citizen for each one; read back what you saved.
- You cannot decide or promise approval. An officer decides within 15 days, otherwise the law
  deems it approved. Never say the pension is granted when it is only submitted.
- The applicant is the person whose mobile number signed in. If someone helps on a shared phone,
  the signed-in mobile must be the applicant's own number.
- Before submit_application you MUST call review_draft, read the summary to the citizen and get
  an explicit yes to the declaration. Pass the draft_hash from the review.
- When done, offer end_session, especially on shared phones or kiosks.
Flow: scheme_info -> update_draft (as details arrive) -> attach_age_proof -> review_draft ->
submit_application -> get_my_application_status later."""

mcp = FastMCP(
    "Sewa Setu - citizen assistant",
    instructions=INSTRUCTIONS,
    token_verifier=C.PortalTokenVerifier(C.CITIZEN_URL, "citizen"),
    auth=AuthSettings(issuer_url=C.PUBLIC_BASE_URL, resource_server_url=C.CITIZEN_URL,
                      required_scopes=["citizen"]),
    stateless_http=True, json_response=True, streamable_http_path="/mcp/citizen",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

FIELDS = ("applicant_name", "dob", "gender", "marital_status", "husband_name",
          "village", "block", "bank_account", "ifsc")
DRAFT_TTL = timedelta(hours=2)   # same as the web form's draft


def _mobile():
    claims = C.current_claims()
    sub = claims["sub"]
    if not sub.startswith("citizen:"):
        raise PermissionError("this server serves citizens only")
    return sub.split(":", 1)[1], claims


def _load_draft(cur, mobile):
    cur.execute("SELECT data, doc_name, doc_path, updated_at FROM agent_drafts WHERE mobile = %s", (mobile,))
    r = cur.fetchone()
    if not r:
        return {}, None, None
    if datetime.now() - r[3] > DRAFT_TTL:
        cur.execute("DELETE FROM agent_drafts WHERE mobile = %s", (mobile,))
        return {}, None, None
    return r[0] or {}, r[1], r[2]


def _save_draft(cur, mobile, data, doc_name, doc_path, client):
    cur.execute("INSERT INTO agent_drafts (mobile, data, doc_name, doc_path, client, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (mobile) DO UPDATE SET data=EXCLUDED.data, "
                "doc_name=EXCLUDED.doc_name, doc_path=EXCLUDED.doc_path, client=EXCLUDED.client, "
                "updated_at=EXCLUDED.updated_at",
                (mobile, json.dumps(data), doc_name, doc_path, client, datetime.now()))


def _draft_hash(data, doc_name):
    return hashlib.sha256(json.dumps([data, doc_name], sort_keys=True).encode()).hexdigest()[:16]


def _existing(cur, mobile):
    r = portal.active_application(cur, mobile)
    return {"application_no": r[1], "status": r[2]} if r else None


def _explain(status):
    return {
        "PENDING": "Submitted and waiting for a block officer to decide.",
        "APPROVED": "Approved by a block officer.",
        "REJECTED": "Rejected by a block officer. The citizen may apply again if they are eligible.",
        "WITHDRAWN": "Withdrawn by the applicant.",
        "DEEMED_APPROVED": "Approved by law: it was not decided within the 15-day time limit.",
    }.get(status, status)


@mcp.tool()
def scheme_info() -> dict:
    """Current rules of the Old Age Pension: who can apply, what is needed, deadlines, valid blocks.
    Read this instead of answering from memory; rules and dates come from the live portal."""
    today = C.portal.now_ist()
    return {
        "scheme": "Old Age Pension, Government of Purvanchal, Department of Social Welfare",
        "amount": "Rs 250 per month",
        "who_can_apply": "Residents aged %d or above on the day of applying (age counted from date of birth)." % portal.MIN_AGE,
        "one_application_rule": "A mobile number can hold only one live application (pending, approved or deemed-approved). "
                                "A rejected or withdrawn one does not block a new application.",
        "application_window_closes": portal.SCHEME_DEADLINE.strftime("%d %b %Y %H:%M IST"),
        "window_open_now": today <= portal.SCHEME_DEADLINE,
        "decision_time_limit_days": portal.SLA_DAYS,
        "if_not_decided": "After %d days a pending application is deemed approved under the RTPS Act." % portal.SLA_DAYS,
        "required_details": {
            "applicant_name": "full name", "dob": "DD/MM/YYYY", "gender": portal.GENDERS,
            "marital_status": portal.MARITAL_STATUSES,
            "husband_name": "late husband's name, only if widowed, optional",
            "village": "village", "block": portal.BLOCKS,
            "bank_account": "9 to 18 digits, the applicant's own account",
            "ifsc": "11 characters like SBIN0003077"},
        "age_proof": "Required: a JPG, PNG or PDF up to 5 MB (e.g. photo of an age-proof document). Use attach_age_proof.",
        "who_decides": "A block officer approves or rejects. This assistant cannot, and cannot promise the outcome.",
        "signed_in_mobile": _mobile()[0],
    }


@mcp.tool()
def check_age_eligibility(dob: str) -> dict:
    """Check the age rule for a date of birth (DD/MM/YYYY) before collecting more details."""
    d = portal.parse_dob(dob)
    if d is None:
        return {"ok": False, "problem": "date of birth must be DD/MM/YYYY"}
    today = C.portal.now_ist().date()
    age = portal.age_on(d, today)
    if age < 0:
        return {"ok": False, "problem": "that date is in the future"}
    if age >= portal.MIN_AGE:
        return {"ok": True, "age": age, "message": "Meets the age rule (%d or above)." % portal.MIN_AGE}
    try:
        turns = d.replace(year=d.year + portal.MIN_AGE)
    except ValueError:
        turns = d.replace(year=d.year + portal.MIN_AGE, day=28)
    return {"ok": False, "age": age,
            "message": "Not eligible yet: the rule is %d or above. Turns %d on %s. Do not submit; "
                       "the portal will refuse it." % (portal.MIN_AGE, portal.MIN_AGE, turns.strftime("%d/%m/%Y"))}


_BAD_TEXT = re.compile(r"[\x00-\x1f\x7f<>{}\[\]`|\\]")


def _field_problems(data):
    probs = {}
    # free-text fields are shown to officers (and their assistants): plain text only, no markup or newlines
    for k in ("applicant_name", "village", "husband_name"):
        if k in data and (_BAD_TEXT.search(data[k] or "") or len(data[k] or "") > 100):
            probs[k] = "plain text only (letters, spaces, . ' -), up to 100 characters"
    if "applicant_name" in data and not portal.sanitize(data["applicant_name"]):
        probs["applicant_name"] = "name is required"
    if "dob" in data:
        d = portal.parse_dob(data["dob"])
        if d is None:
            probs["dob"] = "must be DD/MM/YYYY"
        elif portal.age_on(d, C.portal.now_ist().date()) < portal.MIN_AGE:
            probs["dob"] = "applicant must be %d or above; not eligible" % portal.MIN_AGE
    if "gender" in data and data["gender"] not in portal.GENDERS:
        probs["gender"] = "one of " + ", ".join(portal.GENDERS)
    if "marital_status" in data and data["marital_status"] not in portal.MARITAL_STATUSES:
        probs["marital_status"] = "one of " + ", ".join(portal.MARITAL_STATUSES)
    if "block" in data and data["block"] not in portal.BLOCKS:
        probs["block"] = "one of " + ", ".join(portal.BLOCKS)
    if "bank_account" in data and not re.fullmatch(r"\d{9,18}", re.sub(r"[\s-]", "", data["bank_account"] or "")):
        probs["bank_account"] = "9 to 18 digits"
    if "ifsc" in data and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", re.sub(r"[\s-]", "", data["ifsc"] or "").upper()):
        probs["ifsc"] = "11 characters, e.g. SBIN0003077"
    return probs


@mcp.tool()
def update_draft(applicant_name: str = None, dob: str = None, gender: str = None,
                 marital_status: str = None, husband_name: str = None, village: str = None,
                 block: str = None, bank_account: str = None, ifsc: str = None) -> dict:
    """Save details the citizen has told you into their draft (only the fields you pass change).
    Use exactly what the citizen said; never fill a field yourself. Returns per-field problems and
    what is still missing. Draft is private to the signed-in mobile and expires in 2 hours."""
    mobile, claims = _mobile()
    given = {k: v for k, v in dict(applicant_name=applicant_name, dob=dob, gender=gender,
             marital_status=marital_status, husband_name=husband_name, village=village, block=block,
             bank_account=bank_account, ifsc=ifsc).items() if v is not None}
    conn = C.db(); cur = conn.cursor()
    try:
        ex = _existing(cur, mobile)
        if ex:
            return {"saved": False, "blocked": True,
                    "reason": "This mobile already has application %s (%s). Only one live application is allowed. "
                              "If it is pending and the citizen wants to start over, use withdraw_application first."
                              % (ex["application_no"], ex["status"])}
        data, doc_name, doc_path = _load_draft(cur, mobile)
        probs = _field_problems(given)
        for k, v in given.items():
            if k not in probs:
                data[k] = v.strip()
        _save_draft(cur, mobile, data, doc_name, doc_path, C.client_label(claims))
        conn.commit()
        missing = [f for f in FIELDS if f not in data and f != "husband_name"]
        if not doc_path:
            missing.append("age_proof")
        return {"saved": sorted(k for k in given if k not in probs), "rejected_fields": probs,
                "missing": missing, "draft": data}
    finally:
        cur.close(); conn.close()


@mcp.tool()
def attach_age_proof(filename: str, content_base64: str) -> dict:
    """Attach the age-proof document (JPG, PNG or PDF, at most 5 MB) as base64. The file must really
    be that type. Only call with a document the citizen actually provided."""
    mobile, claims = _mobile()
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ("pdf", "jpg", "jpeg", "png"):
        return {"attached": False, "problem": "Please use a JPG, PNG or PDF file."}
    try:
        raw = base64.b64decode(content_base64, validate=False)
    except Exception:
        return {"attached": False, "problem": "content is not valid base64"}
    if not raw:
        return {"attached": False, "problem": "the file is empty"}
    if len(raw) > 5 * 1024 * 1024:
        return {"attached": False, "problem": "File is larger than 5 MB."}
    magic_ok = (raw[:4] == b"%PDF" if ext == "pdf" else raw[:3] == b"\xff\xd8\xff" if ext in ("jpg", "jpeg")
                else raw[:8] == b"\x89PNG\r\n\x1a\n")
    if not magic_ok:
        return {"attached": False, "problem": "The content does not look like a real .%s file." % ext}
    import os
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:80]
    os.makedirs(portal.UPLOAD_DIR, exist_ok=True)
    path = os.path.join(portal.UPLOAD_DIR, "%s_%s" % (mobile, safe.lower()))
    with open(path, "wb") as fh:
        fh.write(raw)
    conn = C.db(); cur = conn.cursor()
    data, _, _ = _load_draft(cur, mobile)
    _save_draft(cur, mobile, data, safe.lower(), path, C.client_label(claims))
    conn.commit(); cur.close(); conn.close()
    return {"attached": True, "filename": safe.lower(), "size_bytes": len(raw)}


@mcp.tool()
def review_draft() -> dict:
    """Validate the draft with the portal's own rules and return a read-back summary plus a
    draft_hash. Show the summary to the citizen. If can_submit is true and they accept the
    declaration, call submit_application with this draft_hash."""
    mobile, claims = _mobile()
    conn = C.db(); cur = conn.cursor()
    try:
        ex = _existing(cur, mobile)
        data, doc_name, doc_path = _load_draft(cur, mobile)
        conn.commit()
        errors, cleaned = portal.validate_application(data, doc_path or "")
        if ex:
            errors.append("this mobile already has application %s (%s)" % (ex["application_no"], ex["status"]))
        summary = {k: data.get(k, "") for k in FIELDS}
        summary["bank_account"] = C.mask_account(cleaned["bank_account"]) if cleaned["bank_account"] else ""
        summary["age_proof_file"] = doc_name or ""
        summary["mobile"] = mobile
        return {"can_submit": not errors, "problems": errors, "summary": summary,
                "draft_hash": _draft_hash(data, doc_name),
                "declaration_to_read_out": "I declare that the information given is true and that I am "
                    "%d years of age or above. I understand a false declaration can lead to rejection." % portal.MIN_AGE}
    finally:
        cur.close(); conn.close()


@mcp.tool()
def submit_application(draft_hash: str, citizen_accepts_declaration: bool) -> dict:
    """Submit the reviewed draft. Needs the draft_hash from review_draft (so exactly what the citizen
    reviewed is sent) and the citizen's explicit yes to the declaration. Returns the application number."""
    mobile, claims = _mobile()
    if citizen_accepts_declaration is not True:
        return {"submitted": False, "problem": "The citizen must accept the declaration first."}
    conn = C.db(); cur = conn.cursor()
    try:
        data, doc_name, doc_path = _load_draft(cur, mobile)
        if _draft_hash(data, doc_name) != draft_hash:
            return {"submitted": False, "problem": "The draft changed after the review. Call review_draft again and re-confirm."}
        errors, cleaned = portal.validate_application(data, doc_path or "")
        if errors:
            return {"submitted": False, "problems": errors}
        ex = _existing(cur, mobile)
        if ex:
            return {"submitted": False, "problem": "Application %s (%s) already exists for this mobile." % (ex["application_no"], ex["status"])}
        app_id, app_no = portal.create_application(cur, mobile, cleaned)
        cur.execute("INSERT INTO audit_log (application_id, action, actor, note, at) VALUES (%s,'VIA_AI',%s,%s,%s)",
                    (app_id, "applicant:%s" % mobile,
                     "submitted through AI assistant '%s' after citizen's declaration" % C.client_label(claims), datetime.now()))
        cur.execute("DELETE FROM agent_drafts WHERE mobile = %s", (mobile,))
        conn.commit()
    finally:
        cur.close(); conn.close()
    portal.send_sms(mobile, "Sewa Setu: application %s received. Track at the status portal "
                            "with mobile no. and password (DOB as DDMMYYYY)." % app_no)
    return {"submitted": True, "application_no": app_no, "status": "PENDING",
            "what_next": "A block officer will decide within %d days; if nobody decides, the law deems it approved. "
                         "This is NOT yet an approval. An SMS was sent. The status portal password is the date of birth as DDMMYYYY."
                         % portal.SLA_DAYS}


@mcp.tool()
def get_my_application_status() -> dict:
    """The signed-in citizen's applications (newest first) with plain-language status and, for a
    pending one, the date by which the law deems it approved. Only this mobile's data is shown."""
    mobile, _ = _mobile()
    conn = C.db(); cur = conn.cursor()
    cur.execute("SELECT application_no, status, submitted_at, decided_at, decided_by, block, village, applicant_name "
                "FROM applications WHERE mobile = %s ORDER BY submitted_at DESC LIMIT 10", (mobile,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    out = []
    for no, st, sub, dec, by, block, village, name in rows:
        item = {"application_no": no, "applicant_name": name, "status": st, "meaning": _explain(st),
                "submitted_at": sub.strftime("%d %b %Y %H:%M") if sub else None,
                "block": block, "village": village}
        if st == "PENDING" and sub:
            due = sub + timedelta(days=portal.SLA_DAYS)
            item["deemed_approved_on_if_undecided"] = due.strftime("%d %b %Y")
        if dec:
            item["decided_at"] = dec.strftime("%d %b %Y %H:%M")
            item["decided_by"] = "block officer" if st in ("APPROVED", "REJECTED") else ("applicant" if st == "WITHDRAWN" else "automatic (RTPS)")
        out.append(item)
    return {"mobile": mobile, "applications": out or [],
            "note": "No application yet for this mobile." if not out else ""}


@mcp.tool()
def withdraw_application(application_no: str, citizen_confirms: bool) -> dict:
    """Withdraw the citizen's own PENDING application (so they can apply afresh). Only with the
    citizen's explicit confirmation; an approved or rejected application cannot be withdrawn."""
    mobile, claims = _mobile()
    if citizen_confirms is not True:
        return {"withdrawn": False, "problem": "Ask the citizen to confirm that they want to withdraw it."}
    conn = C.db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE applications SET status='WITHDRAWN', decided_at=%s, decided_by='APPLICANT' "
                    "WHERE application_no=%s AND mobile=%s AND status='PENDING' RETURNING id",
                    (datetime.now(), application_no.strip(), mobile))
        r = cur.fetchone()
        if not r:
            return {"withdrawn": False, "problem": "No pending application %s for this mobile." % application_no}
        portal.write_audit(cur, r[0], "WITHDRAW", "applicant:%s" % mobile,
                           "via AI assistant '%s'" % C.client_label(claims))
        conn.commit()
        return {"withdrawn": True, "application_no": application_no,
                "next": "The citizen can now apply again with update_draft."}
    finally:
        cur.close(); conn.close()


@mcp.tool()
def end_session() -> dict:
    """End this sign-in and delete any unsubmitted draft: use on shared phones and kiosks, or when
    the citizen is done. Afterwards this assistant has no access until the citizen signs in again."""
    mobile, claims = _mobile()
    conn = C.db(); cur = conn.cursor()
    cur.execute("DELETE FROM agent_drafts WHERE mobile = %s", (mobile,))
    conn.commit(); cur.close(); conn.close()
    n = C.portal_oauth._revoke_grants("citizen:" + mobile)
    return {"ended": True, "sign_ins_closed": n, "draft_deleted": True}
