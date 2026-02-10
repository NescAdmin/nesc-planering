from __future__ import annotations

from datetime import datetime, timedelta, date
import os
import secrets
import traceback
import uuid
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote, urlencode
import json
import base64
import hashlib
import urllib.request

from jose import jwt

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import select

from .db import init_db, get_session
from .models import (
    Company, User, CompanyMember, Person, UnitType, Project, WorkItem,
    Allocation, UnitAllocation, AdhocAllocation, TimeOff, ProjectComment, ProjectShare, ScheduleBlock
)

APP_NAME = "NESC Planering"
APP_VERSION = ""  # internal build id removed from UI

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="app/templates")

# Make app name/version available in all templates
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["app_version"] = APP_VERSION

# Jinja filters (format dates without year)
def _dm(val):
    try:
        return val.strftime("%d/%m")
    except Exception:
        return str(val)

templates.env.filters["dm"] = _dm
templates.env.filters["urlencode"] = lambda v: quote(str(v))

# Color helpers for readable text on project-colored badges
def _hex_to_rgb(s: str) -> Optional[Tuple[int, int, int]]:
    if not s:
        return None
    s = str(s).strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join([c * 2 for c in h])
        if len(h) == 6:
            try:
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                return (r, g, b)
            except Exception:
                return None
    return None


def _rel_lum(rgb: Tuple[int, int, int]) -> float:
    # https://www.w3.org/TR/WCAG20/#relativeluminancedef
    def _srgb(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def _contrast_fg(color: str) -> str:
    rgb = _hex_to_rgb(color)
    if not rgb:
        return "#0f172a"
    # threshold tuned for UI badges
    return "#ffffff" if _rel_lum(rgb) < 0.55 else "#0f172a"


def _contrast_pill_bg(color: str) -> str:
    fg = _contrast_fg(color)
    return "rgba(255,255,255,.25)" if fg == "#ffffff" else "rgba(0,0,0,.06)"


templates.env.filters["contrast_fg"] = _contrast_fg
templates.env.filters["contrast_pill_bg"] = _contrast_pill_bg

# -------------------------
# Safe parsing helpers (defensive against legacy DB rows with NULLs)
# -------------------------

def _safe_hhmm(val: Optional[str], default: str = "08:00") -> str:
    """Return a sane HH:MM string."""
    if not val:
        return default
    s = str(val).strip()
    try:
        hh, mm = s.split(":")
        hh_i = int(hh)
        mm_i = int(mm)
        if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
            return f"{hh_i:02d}:{mm_i:02d}"
    except Exception:
        pass
    return default


def _safe_workdays(val: Optional[str]) -> str:
    """Return a comma-separated weekday list (Mon=0..Sun=6)."""
    if not val:
        return "0,1,2,3,4"
    s = str(val).strip()
    if not s:
        return "0,1,2,3,4"
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            i = int(part)
            if 0 <= i <= 6:
                out.append(i)
        except Exception:
            continue
    return ",".join(str(i) for i in sorted(set(out))) or "0,1,2,3,4"

# -------------------------
# Friendly redirects for unauthenticated users (HTML routes)
# -------------------------

@app.exception_handler(HTTPException)
async def _handle_http_exception(request: Request, exc: HTTPException):
    # For API routes, keep JSON errors.
    if request.url.path.startswith("/api/"):
        return await fastapi_http_exception_handler(request, exc)

    if exc.status_code == 401:
        # Clear session and redirect to login/setup.
        request.session.pop("uid", None)

        # Decide destination: /setup if no company exists, otherwise /login
        with get_session() as session:
            company = session.exec(select(Company)).first()

        dest = "/setup" if not company else "/login"
        # Preserve intended destination after login
        if dest == "/login":
            next_url = request.url.path
            if request.url.query:
                next_url = next_url + "?" + request.url.query
            dest = f"/login?next={quote(next_url)}"
        return RedirectResponse(dest, status_code=302)

    # Default behavior for other HTTP errors on HTML pages:
    return await fastapi_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _handle_unexpected_exception(request: Request, exc: Exception):
    """Render a readable error page instead of a blank 500.

    We still keep API paths as plain text/json-like output.
    """
    req_id = str(uuid.uuid4())[:8]
    # Log full traceback to server logs for troubleshooting
    traceback.print_exc()

    if request.url.path.startswith("/api/"):
        return PlainTextResponse(f"Internal Server Error (ref {req_id})", status_code=500)

    html = f"""
    <html><head><meta charset='utf-8'><title>Internt fel</title>
    <link rel='stylesheet' href='/static/css/app.css'/></head>
    <body>
      <div class='container narrow' style='margin-top:18px'>
        <h1>Internt serverfel</h1>
        <p class='muted'>Något gick fel i applikationen. Referens: <strong>{req_id}</strong></p>
        <p><a class='btn primary' href='/login'>Till login</a> <a class='btn' href='/setup'>Registrera företag</a></p>
        <p class='muted'>Öppna Render &rarr; <em>Logs</em> och sök på referensen ovan för att se detaljer.</p>
      </div>
    </body></html>
    """
    return HTMLResponse(html, status_code=500)




# -------------------------
# Microsoft Entra ID (Azure AD) auth helpers
# -------------------------

_MS_CACHE: Dict[str, Dict] = {"oidc": {}, "jwks": {}}


def _ms_configured() -> bool:
    # Enable Azure AD auth when AUTH_PROVIDER=azuread and required env vars exist.
    if os.environ.get("AUTH_PROVIDER", "").lower() != "azuread":
        return False
    return bool(os.environ.get("AZURE_CLIENT_ID") and os.environ.get("AZURE_CLIENT_SECRET"))


def _manual_login_enabled() -> bool:
    """Whether the "Admin/felsök" manual user-picker login is enabled.

    Manual login is a legacy/diagnostic fallback and is a security risk in production.

    Rules:
    - If AUTH_PROVIDER=azuread (Microsoft Entra ID): OFF by default. Enable only with ALLOW_MANUAL_LOGIN=1.
    - Otherwise (local/legacy login mode): ON.
    """
    provider = (os.environ.get("AUTH_PROVIDER") or "").strip().lower()
    if provider == "azuread":
        flag = (os.environ.get("ALLOW_MANUAL_LOGIN") or "").strip().lower()
        return flag in ("1", "true", "yes", "on")
    return True


def _public_base_url(request: Request) -> str:
    # Prefer explicit base URL (needed for correct redirect_uri behind proxies)
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}".rstrip("/")


def _ms_tenant() -> str:
    # Use organizations by default (work/school accounts). Can be tenant ID or domain.
    return (os.environ.get("AZURE_TENANT_ID") or "organizations").strip()


def _ms_authority(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}"


def _ms_redirect_uri(request: Request) -> str:
    return _public_base_url(request) + "/auth/microsoft/callback"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _ms_pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _http_post_form(url: str, data: Dict[str, str]) -> Dict:
    body = urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def _http_get_json(url: str) -> Dict:
    with urllib.request.urlopen(url, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def _ms_discovery(tenant: str) -> Dict:
    cached = _MS_CACHE["oidc"].get(tenant)
    if cached and (datetime.now().timestamp() - cached["ts"]) < 3600:
        return cached["val"]
    val = _http_get_json(_ms_authority(tenant) + "/v2.0/.well-known/openid-configuration")
    _MS_CACHE["oidc"][tenant] = {"ts": datetime.now().timestamp(), "val": val}
    return val


def _ms_jwks(tenant: str) -> Dict:
    cached = _MS_CACHE["jwks"].get(tenant)
    if cached and (datetime.now().timestamp() - cached["ts"]) < 3600:
        return cached["val"]
    oidc = _ms_discovery(tenant)
    jwks_uri = oidc.get("jwks_uri")
    if not jwks_uri:
        # Fallback to documented keys endpoint
        jwks_uri = _ms_authority(tenant) + "/discovery/v2.0/keys"
    val = _http_get_json(jwks_uri)
    _MS_CACHE["jwks"][tenant] = {"ts": datetime.now().timestamp(), "val": val}
    return val


def _ms_validate_id_token(id_token: str, client_id: str, expected_nonce: str) -> Dict:
    # Validate signature, audience, issuer, exp, nonce.
    unverified = jwt.get_unverified_claims(id_token)
    tid = unverified.get("tid")
    if not tid:
        raise HTTPException(401, "Microsoft token saknar tenant (tid).")
    issuer = f"https://login.microsoftonline.com/{tid}/v2.0"
    jwks = _ms_jwks(tid)
    claims = jwt.decode(
        id_token,
        jwks,
        algorithms=["RS256"],
        audience=client_id,
        issuer=issuer,
        options={"verify_at_hash": False},
    )
    if claims.get("nonce") != expected_nonce:
        raise HTTPException(401, "Ogiltig nonce i Microsoft-inloggning.")
    return claims


# -------------------------
# Helpers
# -------------------------

def _get_active_user(session, request: Request) -> User:
    # Primary auth: signed session cookie
    try:
        uid = request.session.get("uid")
    except AssertionError:
        # Defensive: if SessionMiddleware is not in the stack, treat as not logged in.
        uid = None
    if not uid:
        # Dev escape hatch: ?as_user=<id> (also writes session)
        uid_q = request.query_params.get("as_user")
        if uid_q:
            try:
                uid = int(uid_q)
            except Exception:
                raise HTTPException(400, "Invalid user id")
            request.session["uid"] = uid
        else:
            raise HTTPException(401, "Login required")

    u = session.get(User, int(uid))
    if not u:
        request.session.pop("uid", None)
        raise HTTPException(401, "Login required")
    return u







# --- Membership helpers (status gate) ---

def _get_company_member(session, company_id: int, user_id: int):
    return session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == user_id,
        )
    ).first()


def _member_status(mem) -> str:
    st = (getattr(mem, "status", None) if mem else None) or "active"
    st = str(st).lower().strip()
    return st if st in ("active", "pending", "disabled") else "active"


@app.middleware("http")
async def _member_status_gate(request: Request, call_next):
    path = request.url.path
    # Public paths
    if path.startswith("/static/") or path.startswith("/auth/microsoft"):
        return await call_next(request)
    if path in ("/login", "/setup", "/logout", "/pending"): 
        return await call_next(request)
    if path in ("/favicon.ico",):
        return await call_next(request)

    try:
        uid = request.session.get("uid")
    except Exception:
        uid = None
    if not uid:
        return await call_next(request)

    try:
        uid_i = int(uid)
    except Exception:
        return await call_next(request)

    with get_session() as session:
        company = session.exec(select(Company)).first()
        if not company:
            return await call_next(request)
        mem = _get_company_member(session, company.id, uid_i)
        status = _member_status(mem) if mem else "pending"
        u = session.get(User, uid_i)
        is_admin = False
        if u and u.role == "admin":
            is_admin = True
        if mem and mem.role_in_company == "admin":
            is_admin = True
        if status != "active" and not is_admin:
            if path.startswith("/api/"):
                return JSONResponse(status_code=403, content={"error": "member_inactive", "status": status})
            if path != "/pending":
                return RedirectResponse("/pending", status_code=302)

    return await call_next(request)


def _company_member_role(session, company_id: int, user_id: int) -> str:
    mem = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == user_id,
        )
    ).first()
    return mem.role_in_company if mem else ""


def _is_admin(session, company: Optional[Company], user: Optional[User]) -> bool:
    if not company or not user:
        return False
    if user.role == "admin":
        return True
    return _company_member_role(session, company.id, user.id) == "admin"


def _is_planner_or_admin(session, company: Optional[Company], user: Optional[User]) -> bool:
    if not company or not user:
        return False
    if user.role in ("admin", "planner"):
        return True
    return _company_member_role(session, company.id, user.id) in ("admin", "planner")


def _require_admin(session, company: Optional[Company], user: Optional[User]) -> None:
    if not _is_admin(session, company, user):
        raise HTTPException(403, "Admin required")


def _require_planner_or_admin(session, company: Optional[Company], user: Optional[User]) -> None:
    if not _is_planner_or_admin(session, company, user):
        raise HTTPException(403, "Planner/Admin required")


def _get_active_company(session, request: Request) -> Optional[Company]:
    cid = request.query_params.get("company_id")
    if cid:
        return session.get(Company, int(cid))
    return session.exec(select(Company)).first()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_bucket(d: date) -> Tuple[date, date]:
    """Return (start,end) for the work-week bucket (Mon–Fri) containing d."""
    s = _week_start(d)
    e = s + timedelta(days=4)
    return s, e


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(d: date) -> date:
    # end of month
    if d.month == 12:
        nxt = d.replace(year=d.year + 1, month=1, day=1)
    else:
        nxt = d.replace(month=d.month + 1, day=1)
    return nxt - timedelta(days=1)


def _parse_ref(ref: Optional[str]) -> date:
    if ref:
        return date.fromisoformat(ref)
    return datetime.now().date()


def _periods(view: str, ref: date) -> List[Dict]:
    # returns list of dicts with label,start,end
    view = view or "week"
    if view == "day":
        ws = _week_start(ref)
        out = []
        # Day view = current work-week (Mon–Fri)
        for i in range(5):
            d0 = ws + timedelta(days=i)
            out.append({"label": ["Måndag","Tisdag","Onsdag","Torsdag","Fredag"][i], "start": d0, "end": d0})
        return out

    if view == "month":
        months_sv = ["Jan","Feb","Mar","Apr","Maj","Jun","Jul","Aug","Sep","Okt","Nov","Dec"]
        ms = _month_start(ref)
        out = []
        cur = ms
        # Month view: show 12 months at a glance
        for _ in range(12):
            s = _month_start(cur)
            e = _month_end(cur)
            out.append({"label": months_sv[s.month - 1], "start": s, "end": e})
            # next month
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1, day=1)
            else:
                cur = cur.replace(month=cur.month + 1, day=1)
        return out

    # week (default): show more weeks at a glance
    ws = _week_start(ref)
    out = []
    cur = ws
    for _ in range(5):
        s = cur
        e = cur + timedelta(days=4)
        out.append({"label": f"v{s.isocalendar().week}", "start": s, "end": e})
        cur = cur + timedelta(days=7)
    return out


def _date_range_start_end(periods: List[Dict]) -> Tuple[date, date]:
    return periods[0]["start"], periods[-1]["end"]


def _timeoff_overlaps(session, person_id: int, start: date, end: date) -> List[TimeOff]:
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    return session.exec(
        select(TimeOff).where(TimeOff.person_id == person_id, TimeOff.end > start_dt, TimeOff.start < end_dt)
    ).all()


def _hours_per_day(person: Person) -> float:
    def to_min(hhmm: str) -> int:
        hhmm = _safe_hhmm(hhmm, "08:00")
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    return max(0, (to_min(getattr(person, "work_end", None)) - to_min(getattr(person, "work_start", None))) / 60.0)


def _is_workday(person: Person, d0: date) -> bool:
    wd = _safe_workdays(getattr(person, "work_days", None))
    allowed = {int(x) for x in wd.split(",") if x.strip()}
    return d0.weekday() in allowed


def _capacity_hours_in_range(session, person: Person, start: date, end: date) -> float:
    # Capacity should be 40h/week by default (8h/day) even if display hours are 08:00–17:00.
    # We model a 60 min lunch break in _workday_minutes().
    hrs_day = _workday_minutes(person) / 60.0
    offs = _timeoff_overlaps(session, person.id, start, end)
    off_dates = set()
    for o in offs:
        cur = o.start.date()
        last = (o.end - timedelta(seconds=1)).date()
        while cur <= last:
            off_dates.add(cur)
            cur += timedelta(days=1)

    total = 0.0
    cur = start
    while cur <= end:
        if _is_workday(person, cur) and cur not in off_dates:
            total += hrs_day
        cur += timedelta(days=1)
    return total


def _allocations_in_range(session, company_id: int, start: date, end: date) -> List[Allocation]:
    return session.exec(
        select(Allocation).where(
            Allocation.company_id == company_id,
            Allocation.end_date >= start,
            Allocation.start_date <= end,
        )
    ).all()


def _project_totals(session, project_id: int) -> int:
    p = session.get(Project, int(project_id))
    if p is not None and int(getattr(p, "budget_minutes", 0) or 0) > 0:
        return int(getattr(p, "budget_minutes", 0) or 0)
    items = session.exec(select(WorkItem).where(WorkItem.project_id == project_id)).all()
    return sum(int(i.total_minutes or 0) for i in items)

def _workday_minutes(person: Person) -> int:
    # Treat 08:00–17:00 as 8h/day by default (60 min lunch)
    def to_min(hhmm: str) -> int:
        hhmm = _safe_hhmm(hhmm, "08:00")
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    raw = max(0, to_min(getattr(person, "work_end", None)) - to_min(getattr(person, "work_start", None)))
    # Lunch/paus: 60 min (MVP)
    return max(0, raw - 60)


def _planned_minutes_from_allocations(session, company_id: int, project_id: int) -> int:
    people = {p.id: p for p in session.exec(select(Person).where(Person.company_id == company_id)).all()}
    allocs = session.exec(select(Allocation).where(Allocation.company_id == company_id, Allocation.project_id == project_id)).all()
    total = 0
    for a in allocs:
        person = people.get(a.person_id)
        if not person:
            continue
        day_min = _workday_minutes(person)
        # Iterate workdays (Mon–Fri as per person.work_days)
        wd = _safe_workdays(getattr(person, "work_days", None))
        allowed = {int(x) for x in wd.split(",") if x.strip() != ""}
        cur = a.start_date
        while cur <= a.end_date:
            if cur.weekday() in allowed:
                total += int(day_min * (a.percent / 100.0))
            cur = cur + timedelta(days=1)
    return total


def _planned_minutes_from_unit_allocations(session, project_id: int) -> int:
    uas = session.exec(select(UnitAllocation).where(UnitAllocation.project_id == project_id)).all()
    if not uas:
        return 0
    # UnitAllocation is time-based (minutes)
    total = 0
    for ua in uas:
        total += int(getattr(ua, "minutes", 0) or 0)
    return total


def _project_scope_planned(session, company_id: int, project_id: int) -> Dict[str, int]:
    scope = _project_totals(session, project_id)
    planned_pct = _planned_minutes_from_allocations(session, company_id, project_id)
    planned_units = _planned_minutes_from_unit_allocations(session, project_id)
    planned = planned_pct + planned_units
    return {"scope": scope, "planned": planned, "planned_pct": planned_pct, "planned_units": planned_units, "over": max(0, planned - scope)}



# -------------------------
# Startup
# -------------------------

@app.on_event("startup")
def on_startup():
    init_db()


# -------------------------
# Auth (simple session-based login)
# -------------------------

@app.get("/healthz")
def healthz():
    return {"ok": True, "name": APP_NAME, "version": APP_VERSION}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path or "/"

    # Public paths
    if (
        path.startswith("/static")
        or path.startswith("/shared")
        or path.startswith("/login")
        or path.startswith("/logout")
        or path.startswith("/auth/microsoft")
        or path.startswith("/setup")
        or path.startswith("/healthz")
        or path == "/favicon.ico"
        or path == "/robots.txt"
        or path == "/manifest.json"
    ):
        return await call_next(request)

    # Session cookie
    try:
        uid = request.session.get("uid")
    except AssertionError:
        # Defensive: if SessionMiddleware is not in the stack, treat as not logged in.
        uid = None

    # If no company exists yet, force setup (but keep /setup open above)
    if not uid:
        with get_session() as session:
            company_exists = session.exec(select(Company)).first() is not None
        if not company_exists:
            return RedirectResponse("/setup", status_code=302)

        # API calls should return JSON auth error
        if path.startswith("/api/"):
            return JSONResponse({"error": "auth_required"}, status_code=401)

        # HTML pages redirect to login
        nxt = str(request.url.path)
        if request.url.query:
            nxt += "?" + request.url.query
        return RedirectResponse(f"/login?next={quote(nxt)}", status_code=302)

    return await call_next(request)


# Cookie-based sessions (signed). Must wrap other middlewares that access request.session.
# In production, set SECRET_KEY on Render (Environment).
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    same_site="lax",
)

# -------------------------
# Setup / Company
# -------------------------

@app.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request):
    with get_session() as session:
        if session.exec(select(Company)).first():
            return RedirectResponse("/login", status_code=302)
        users = session.exec(select(User)).all()
        prefill_name = request.session.get("setup_prefill_name")
        prefill_email = request.session.get("setup_prefill_email")
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "users": users,
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "active_user": None,
                "prefill_admin_name": prefill_name,
                "prefill_admin_email": prefill_email,
            },
        )


@app.post("/setup")
def setup_post(
    request: Request,
    company_name: str = Form(...),
    work_start: str = Form("08:00"),
    work_end: str = Form("17:00"),
    member_user_ids: Optional[List[int]] = Form(None),
    admin_name: Optional[str] = Form(None),
    admin_email: Optional[str] = Form(None),
    unit_names: Optional[List[str]] = Form(None),
    unit_minutes: Optional[List[str]] = Form(None),
    unit_icons: Optional[List[str]] = Form(None),
):
    with get_session() as session:
        if session.exec(select(Company)).first():
            return RedirectResponse("/", status_code=302)

        # If you arrived here after a Microsoft login (before a company existed),
        # we can auto-fill the first admin from the session if the form left it blank.
        if not admin_name:
            admin_name = request.session.get("setup_prefill_name")
        if not admin_email:
            admin_email = request.session.get("setup_prefill_email")

        c = Company(name=company_name.strip(), work_start=work_start, work_end=work_end)
        session.add(c)
        session.commit()
        session.refresh(c)

        member_user_ids = member_user_ids or []
        all_users = session.exec(select(User)).all()
        users_by_id = {u.id: u for u in all_users}

        created_admin: Optional[User] = None

        # If there are no users in the database yet, create the first admin.
        if not all_users:
            if not (admin_name and admin_email):
                # Cannot proceed without an admin user.
                raise HTTPException(400, "No users exist. Provide admin_name and admin_email.")
            created_admin = User(name=admin_name.strip(), email=admin_email.strip().lower(), role="admin")
            session.add(created_admin)
            session.commit()
            session.refresh(created_admin)
            users_by_id[created_admin.id] = created_admin
            member_user_ids = [created_admin.id]

        # If users exist but none were selected, default to including all users.
        if all_users and not member_user_ids:
            member_user_ids = [u.id for u in all_users]

        # Create company members + persons
        login_uid: Optional[int] = None
        for uid in member_user_ids:
            u = users_by_id.get(uid)
            if not u:
                continue
            role_in_company = "admin" if u.role == "admin" else ("planner" if u.role == "planner" else "employee")
            session.add(CompanyMember(company_id=c.id, user_id=uid, role_in_company=role_in_company))
            if u.role != "external":
                session.add(
                    Person(
                        company_id=c.id,
                        user_id=uid,
                        work_start=_safe_hhmm(getattr(c, "work_start", None), "08:00"),
                        work_end=_safe_hhmm(getattr(c, "work_end", None), "17:00"),
                        work_days=_safe_workdays(getattr(c, "work_days", None)),
                    )
                )
            # Prefer an admin as default login user
            if login_uid is None or role_in_company == "admin":
                login_uid = uid

        session.commit()

        # Auto-login after setup (so you can continue immediately)
        if login_uid:
            request.session["uid"] = int(login_uid)
            # Setup completed -> clear prefill
            request.session.pop("setup_prefill_name", None)
            request.session.pop("setup_prefill_email", None)

        if unit_names:
            unit_minutes = unit_minutes or []
            unit_icons = unit_icons or []
            for i, name in enumerate(unit_names):
                if not name or not str(name).strip():
                    continue
                # minutes can be blank; default to 60
                minutes_raw = unit_minutes[i] if i < len(unit_minutes) else ""
                try:
                    minutes = int(str(minutes_raw).strip()) if str(minutes_raw).strip() else 60
                except Exception:
                    minutes = 60
                icon = unit_icons[i] if i < len(unit_icons) and unit_icons[i] else "■"
                session.add(UnitType(company_id=c.id, name=name.strip(), default_minutes=minutes, icon=icon))

        session.commit()
        return RedirectResponse("/", status_code=302)


# -------------------------
# Login / Logout
# -------------------------


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    with get_session() as session:
        company = session.exec(select(Company)).first()
        if not company:
            # No company yet: go to setup. (Microsoft callback can prefill admin fields.)
            return RedirectResponse("/setup", status_code=302)

        next_url = request.query_params.get("next", "") or ""

        members = session.exec(select(CompanyMember).where(CompanyMember.company_id == company.id)).all()
        member_ids = [m.user_id for m in members]
        if member_ids:
            users = session.exec(select(User).where(User.id.in_(member_ids))).all()
        else:
            users = session.exec(select(User)).all()

        users = sorted(users, key=lambda u: (u.name or u.email or ""))
        ms_provider_requested = os.environ.get("AUTH_PROVIDER", "").lower() == "azuread"
        ms_configured = _ms_configured()
        manual_login_enabled = _manual_login_enabled()

        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "company": company,
                "users": users,
                "next": next_url,
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "ms_provider_requested": ms_provider_requested,
                "ms_configured": ms_configured,
                "manual_login_enabled": manual_login_enabled,
                "active_user": None,
            },
        )


@app.post("/login")
def login_post(request: Request, user_id: int = Form(...), next: str = Form("")):
    # Manual login (user picker) is a legacy/diagnostic fallback.
    # When Azure AD is configured, it must be disabled by default to prevent impersonation.
    if not _manual_login_enabled():
        raise HTTPException(403, "Manuell inloggning är avstängd. Logga in med Microsoft.")

    with get_session() as session:
        u = session.get(User, int(user_id))
        if not u:
            raise HTTPException(404, "User not found")
        request.session["uid"] = u.id

    return RedirectResponse(next or "/", status_code=302)


# -------------------------
# Microsoft Entra ID login
# -------------------------


@app.get("/auth/microsoft")
def ms_login_start(request: Request, next: str = ""):
    if not _ms_configured():
        raise HTTPException(500, "Microsoft-inloggning är inte konfigurerad (saknar AZURE_CLIENT_ID/SECRET eller AUTH_PROVIDER=azuread).")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _ms_pkce_pair()

    request.session["ms_state"] = state
    request.session["ms_nonce"] = nonce
    request.session["ms_verifier"] = verifier
    request.session["ms_next"] = next or "/"

    tenant = _ms_tenant()
    client_id = os.environ.get("AZURE_CLIENT_ID")
    redirect_uri = _ms_redirect_uri(request)

    authorize = _ms_authority(tenant) + "/oauth2/v2.0/authorize"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return RedirectResponse(authorize + "?" + urlencode(params), status_code=302)


@app.get("/auth/microsoft/callback")
def ms_login_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    if error:
        msg = (error_description or error or "Microsoft-inloggning misslyckades.").strip()
        raise HTTPException(401, msg)

    expected_state = request.session.get("ms_state")
    expected_nonce = request.session.get("ms_nonce")
    verifier = request.session.get("ms_verifier")
    next_url = request.session.get("ms_next") or "/"

    # One-shot values
    request.session.pop("ms_state", None)
    request.session.pop("ms_nonce", None)
    request.session.pop("ms_verifier", None)
    request.session.pop("ms_next", None)

    if not code or not state or not expected_state or state != expected_state:
        raise HTTPException(401, "Ogiltig inloggningssession (state). Försök igen.")

    if not verifier or not expected_nonce:
        raise HTTPException(401, "Ogiltig inloggningssession. Försök igen.")

    tenant = _ms_tenant()
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    redirect_uri = _ms_redirect_uri(request)

    token_url = _ms_authority(tenant) + "/oauth2/v2.0/token"
    token = _http_post_form(
        token_url,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": "openid profile email",
        },
    )

    id_token = token.get("id_token")
    if not id_token:
        raise HTTPException(401, "Microsoft svarade utan id_token.")

    claims = _ms_validate_id_token(id_token, client_id, expected_nonce)

    email = (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("upn")
        or claims.get("unique_name")
        or ""
    ).strip()
    name = (claims.get("name") or "").strip()
    tid = (claims.get("tid") or "").strip()

    if not email:
        raise HTTPException(401, "Microsoft-inloggning saknar e-post/username.")

    allowed_domain = (os.environ.get("AZURE_ALLOWED_DOMAIN") or "").strip().lower().lstrip("@")
    if allowed_domain and not email.lower().endswith("@" + allowed_domain):
        raise HTTPException(403, "E-postdomän är inte tillåten för denna tjänst.")

    with get_session() as session:
        company = session.exec(select(Company)).first()
        if not company:
            # No company yet: send the user to /setup and prefill the first admin.
            request.session["setup_prefill_name"] = name or (email.split("@")[0] if email else "")
            request.session["setup_prefill_email"] = email
            return RedirectResponse("/setup", status_code=302)

        u = session.exec(select(User).where(User.email == email)).first()

        if not u:
            # If there are no users/members yet, make the first one admin so you don't lock yourself out.
            is_first = session.exec(select(User)).first() is None
            role = "admin" if is_first else "employee"
            u = User(name=name or email.split("@")[0], email=email, role=role)
            session.add(u)
            session.commit()
            session.refresh(u)

        mem = session.exec(
            select(CompanyMember).where(CompanyMember.company_id == company.id, CompanyMember.user_id == u.id)
        ).first()
        if not mem:
            role_in_company = "admin" if u.role == "admin" else ("planner" if u.role == "planner" else "employee")
            status = "active" if role_in_company == "admin" else "pending"
            session.add(CompanyMember(company_id=company.id, user_id=u.id, role_in_company=role_in_company, status=status))
            # Create planning resource unless external. We allow pending/disabled members to exist as people
            # so admins can plan for them, but they cannot access the app until activated.
            if u.role != "external":
                session.add(
                    Person(
                        company_id=company.id,
                        user_id=u.id,
                        work_start=_safe_hhmm(getattr(company, "work_start", None), "08:00"),
                        work_end=_safe_hhmm(getattr(company, "work_end", None), "17:00"),
                        work_days=_safe_workdays(getattr(company, "work_days", None)),
                    )
                )
            session.commit()
        else:
            # Backward compatible default
            if not getattr(mem, "status", None):
                mem.status = "active"
                session.add(mem)
                session.commit()


        # Logged in
        request.session["uid"] = u.id
        request.session["auth_provider"] = "azuread"
        request.session["ms_tid"] = tid

    return RedirectResponse(next_url or "/", status_code=302)


@app.get("/logout")
def logout(request: Request):
    provider = request.session.get("auth_provider")
    tid = request.session.get("ms_tid") or _ms_tenant()
    request.session.clear()

    # If logged in via Microsoft, also sign out from Entra ID
    if provider == "azuread":
        post_logout = _public_base_url(request) + "/login"
        url = _ms_authority(tid) + "/oauth2/v2.0/logout?" + urlencode({"post_logout_redirect_uri": post_logout})
        return RedirectResponse(url, status_code=302)

    return RedirectResponse("/login", status_code=302)


@app.get("/pending", response_class=HTMLResponse)
def pending_view(request: Request):
    """Shown when a user is a member but not yet activated (pending/disabled)."""
    with get_session() as session:
        company = session.exec(select(Company)).first()
        active_user = None
        status = ""
        try:
            if request.session.get("uid"):
                active_user = _get_active_user(session, request)
        except Exception:
            active_user = None

        if company and active_user:
            mem = _get_company_member(session, company.id, active_user.id)
            status = _member_status(mem) if mem else "pending"

        return templates.TemplateResponse(
            "pending.html",
            {
                "request": request,
                "company": company,
                "active_user": active_user,
                "status": status,
            },
        )


@app.get("/company", response_class=HTMLResponse)
def company_admin(request: Request):
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        # If not logged in, go to login (base URL should always work).
        if not request.session.get("uid"):
            return RedirectResponse(f"/login?next={quote(str(request.url.path + (('?' + request.url.query) if request.url.query else '')))}", status_code=302)

        try:
            user = _get_active_user(session, request)
        except HTTPException:
            return RedirectResponse("/login", status_code=302)

        members = session.exec(select(CompanyMember).where(CompanyMember.company_id == company.id)).all()
        users = {u.id: u for u in session.exec(select(User)).all()}
        units = session.exec(select(UnitType).where(UnitType.company_id == company.id)).all()

        err = request.query_params.get("err", "") or ""

        return templates.TemplateResponse(
            "company.html",
            {
                "request": request,
                "active_user": user,
                "company": company,
                "members": members,
                "users": users,
                "units": units,
                "err": err,
                "is_admin": _is_admin(session, company, user),
            },
        )

@app.post("/company/units/add")
def company_units_add(
    request: Request,
    name: str = Form(...),
    minutes: int = Form(...),
    icon: str = Form("■"),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        _require_admin(session, company, user)

        nm = (name or "").strip()
        if not nm:
            return RedirectResponse("/company", status_code=302)

        session.add(UnitType(company_id=company.id, name=nm, default_minutes=int(minutes), icon=icon or "■"))
        session.commit()
        return RedirectResponse("/company", status_code=302)


@app.post("/company/units/{unit_id}/delete")
def company_units_delete(request: Request, unit_id: int):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        _require_admin(session, company, user)

        ut = session.get(UnitType, int(unit_id))
        if not ut or ut.company_id != company.id:
            raise HTTPException(404, "UnitType not found")

        used = session.exec(
            select(WorkItem)
            .join(Project, WorkItem.project_id == Project.id)
            .where(Project.company_id == company.id, WorkItem.unit_type_id == ut.id)
        ).first()

        if used:
            return RedirectResponse("/company?err=unit_in_use", status_code=302)

        session.delete(ut)
        session.commit()
        return RedirectResponse("/company", status_code=302)


@app.post("/company/members/add")
def company_members_add(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role_in_company: str = Form("employee"),
):
    with get_session() as session:
        active = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        _require_admin(session, company, active)

        email = (email or "").strip().lower()
        name = (name or "").strip()
        role_in_company = (role_in_company or "employee").strip().lower()

        if role_in_company not in ("admin", "planner", "employee", "external"):
            role_in_company = "employee"

        if not email:
            return RedirectResponse("/company", status_code=302)

        u = session.exec(select(User).where(User.email == email)).first()
        if not u:
            u = User(email=email, name=name or email, role=role_in_company)
            session.add(u)
            session.flush()
        else:
            if name and (not u.name or u.name.strip() == ""):
                u.name = name

        mem = session.exec(
            select(CompanyMember).where(CompanyMember.company_id == company.id, CompanyMember.user_id == u.id)
        ).first()
        if not mem:
            mem = CompanyMember(company_id=company.id, user_id=u.id, role_in_company=role_in_company)
            session.add(mem)
        else:
            mem.role_in_company = role_in_company

        # Create planning resource unless external
        if role_in_company != "external":
            p = session.exec(select(Person).where(Person.company_id == company.id, Person.user_id == u.id)).first()
            if not p:
                p = Person(
                    company_id=company.id,
                    user_id=u.id,
                    work_start=_safe_hhmm(getattr(company, "work_start", None), "08:00"),
                    work_end=_safe_hhmm(getattr(company, "work_end", None), "17:00"),
                    work_days=_safe_workdays(getattr(company, "work_days", None)),
                )
                session.add(p)

        session.commit()
        return RedirectResponse("/company", status_code=302)


@app.post("/company/members/{member_id}/update")
def company_members_update(
    request: Request,
    member_id: int,
    role_in_company: str = Form("employee"),
    status: str = Form("active"),
):
    with get_session() as session:
        active = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        _require_admin(session, company, active)

        mem = session.get(CompanyMember, int(member_id))
        if not mem or mem.company_id != company.id:
            raise HTTPException(404, "Member not found")

        role_in_company = (role_in_company or "employee").strip().lower()
        if role_in_company not in ("admin", "planner", "employee", "external"):
            role_in_company = "employee"

        status = (status or "active").strip().lower()
        if status not in ("active", "pending", "disabled"):
            status = "active"

        # Prevent self-lockout: do not allow an admin to set themselves to pending/disabled
        if mem.user_id == active.id and status != "active":
            return RedirectResponse("/company?err=self_lock", status_code=302)

        mem.role_in_company = role_in_company
        mem.status = status

        # Keep User.role roughly in sync (single-company MVP)
        u = session.get(User, int(mem.user_id))
        if u:
            u.role = role_in_company

        # NOTE: We do NOT delete planning data when changing status/role.
        # If a member is active and not external, ensure they have a Person row.
        if role_in_company != "external":
            p = session.exec(select(Person).where(Person.company_id == company.id, Person.user_id == mem.user_id)).first()
            if not p:
                p = Person(
                    company_id=company.id,
                    user_id=mem.user_id,
                    work_start=_safe_hhmm(getattr(company, "work_start", None), "08:00"),
                    work_end=_safe_hhmm(getattr(company, "work_end", None), "17:00"),
                    work_days=_safe_workdays(getattr(company, "work_days", None)),
                )
                session.add(p)

        session.commit()
        return RedirectResponse("/company", status_code=302)


@app.post("/company/members/{member_id}/delete")
def company_members_delete(request: Request, member_id: int):
    with get_session() as session:
        active = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        _require_admin(session, company, active)

        mem = session.get(CompanyMember, int(member_id))
        if not mem or mem.company_id != company.id:
            raise HTTPException(404, "Member not found")

        person = session.exec(select(Person).where(Person.company_id == company.id, Person.user_id == mem.user_id)).first()

        if person:
            # allocations (%)
            for a in session.exec(
                select(Allocation).where(Allocation.company_id == company.id, Allocation.person_id == person.id)
            ).all():
                session.delete(a)

            # unit allocations within this company's projects
            proj_ids = [p.id for p in session.exec(select(Project).where(Project.company_id == company.id)).all()]
            if proj_ids:
                for ua in session.exec(
                    select(UnitAllocation).where(UnitAllocation.person_id == person.id, UnitAllocation.project_id.in_(proj_ids))
                ).all():
                    session.delete(ua)

            # time off
            for o in session.exec(select(TimeOff).where(TimeOff.person_id == person.id)).all():
                session.delete(o)

            # schedule blocks (if any)
            for sb in session.exec(select(ScheduleBlock).where(ScheduleBlock.person_id == person.id)).all():
                session.delete(sb)

            session.delete(person)

        session.delete(mem)
        session.commit()
        return RedirectResponse("/company", status_code=302)




# -------------------------
# Time off (admin)
# -------------------------

@app.get("/timeoff", response_class=HTMLResponse)
def timeoff_view(request: Request):
    with get_session() as session:
        user=_get_active_user(session, request)
        company=_get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_admin(session, company, user)

        people=session.exec(select(Person).where(Person.company_id==company.id)).all()
        users={u.id:u for u in session.exec(select(User)).all()}
        person_ids=[p.id for p in people]
        offs=session.exec(select(TimeOff).where(TimeOff.person_id.in_(person_ids)).order_by(TimeOff.start.desc())).all() if person_ids else []
        return templates.TemplateResponse("timeoff.html", {"request":request,"active_user":user,"company":company,"people":people,"users":users,"offs":offs,"is_admin":True})


@app.post("/timeoff/new")
def timeoff_new(request: Request, person_id: int = Form(...), start: str = Form(...), end: str = Form(...), kind: str = Form("leave"), note: str = Form("")):
    with get_session() as session:
        user=_get_active_user(session, request)
        company=_get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_admin(session, company, user)
        person=session.get(Person, int(person_id))
        if not person or person.company_id!=company.id:
            raise HTTPException(404, "Person not found")
        try:
            sdt=datetime.fromisoformat(start)
            edt=datetime.fromisoformat(end)
        except Exception:
            raise HTTPException(400, "Start/end must be ISO datetime")
        if edt <= sdt:
            raise HTTPException(400, "End must be after start")
        session.add(TimeOff(person_id=person.id, start=sdt, end=edt, kind=(kind or "leave"), note=(note or "").strip()))
        session.commit()
        return RedirectResponse("/timeoff", status_code=302)


@app.post("/timeoff/{timeoff_id}/delete")
def timeoff_delete(request: Request, timeoff_id: int):
    with get_session() as session:
        user=_get_active_user(session, request)
        company=_get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_admin(session, company, user)
        off=session.get(TimeOff, int(timeoff_id))
        if not off:
            raise HTTPException(404, "TimeOff not found")
        person=session.get(Person, off.person_id)
        if not person or person.company_id!=company.id:
            raise HTTPException(404, "TimeOff not found")
        session.delete(off)
        session.commit()
        return RedirectResponse("/timeoff", status_code=302)


# -------------------------
# Projects
# -------------------------

@app.get("/projects", response_class=HTMLResponse)
def projects_list(request: Request, tab: str = "active"):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        tab = (tab or "active").lower().strip()
        if tab not in ("active", "closed"):
            tab = "active"

        all_projects = session.exec(select(Project).where(Project.company_id == company.id)).all()
        counts = {
            "active": sum(1 for p in all_projects if (p.status or "active") == "active"),
            "closed": sum(1 for p in all_projects if (p.status or "active") == "closed"),
        }

        projects = [p for p in all_projects if (p.status or "active") == tab]
        totals = {p.id: _project_scope_planned(session, company.id, p.id) for p in projects}

        return templates.TemplateResponse(
            "projects.html",
            {
                "request": request,
                "active_user": user,
                "company": company,
                "projects": projects,
                "totals": totals,
                "tab": tab,
                "counts": counts,
                "is_admin": _is_admin(session, company, user),
                "can_manage": _is_planner_or_admin(session, company, user),
            },
        )


@app.get("/projects/new", response_class=HTMLResponse)
def projects_new_get(request: Request):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        units = session.exec(select(UnitType).where(UnitType.company_id == company.id)).all()
        return templates.TemplateResponse(
            "project_new.html",
            {"request": request, "active_user": user, "company": company, "units": units},
        )


def _pick_color(n: int) -> str:
    palette = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC"]
    return palette[n % len(palette)]


@app.post("/projects/new")
def projects_new_post(
    request: Request,
    name: str = Form(...),
    mode: str = Form("budget"),
    budget_hours: str = Form(""),
    unit_type_ids: Optional[List[int]] = Form(None),
    quantities: Optional[List[int]] = Form(None),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        existing_count = len(session.exec(select(Project).where(Project.company_id == company.id)).all())
        color = _pick_color(existing_count)

        mode = (mode or "budget").strip().lower()

        # Budget-first workflow: if mode=budget, we store total budget hours (minutes) and do not create units.
        budget_minutes = 0
        if mode == "budget":
            try:
                bh = float(str(budget_hours or "").replace(",", ".").strip())
            except Exception:
                bh = 0.0
            budget_minutes = int(round(max(0.0, bh) * 60))
            if budget_minutes <= 0:
                units = session.exec(select(UnitType).where(UnitType.company_id == company.id)).all()
                return templates.TemplateResponse(
                    "project_new.html",
                    {
                        "request": request,
                        "active_user": user,
                        "company": company,
                        "units": units,
                        "error": "Ange en budget (timmar) för projektet.",
                        "prefill_name": name,
                        "prefill_budget_hours": budget_hours,
                        "prefill_mode": mode,
                    },
                    status_code=400,
                )

        p = Project(
            company_id=company.id,
            name=name.strip(),
            color=color,
            owner_user_id=user.id,
            budget_minutes=budget_minutes,
        )
        session.add(p)
        session.commit()
        session.refresh(p)

        # Units/scope mode (optional): create WorkItems from the unit catalog.
        if mode != "budget":
            units = {u.id: u for u in session.exec(select(UnitType).where(UnitType.company_id == company.id)).all()}
            unit_type_ids = unit_type_ids or []
            quantities = quantities or []

            for i, utid in enumerate(unit_type_ids):
                qty = int(quantities[i]) if i < len(quantities) else 0
                if qty <= 0:
                    continue
                ut = units.get(int(utid))
                if not ut:
                    continue
                minutes_per = ut.default_minutes
                total = qty * minutes_per
                wi = WorkItem(
                    project_id=p.id,
                    unit_type_id=ut.id,
                    title=ut.name,
                    quantity=qty,
                    minutes_per_unit=minutes_per,
                    total_minutes=total,
                )
                session.add(wi)

        session.commit()
        return RedirectResponse(f"/projects/{p.id}", status_code=302)


@app.post("/projects/{project_id}/status")
def project_set_status(
    request: Request,
    project_id: int,
    status: str = Form(...),
    tab: str = Form("active"),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_planner_or_admin(session, company, user)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        status = (status or "active").lower().strip()
        if status not in ("active", "closed"):
            raise HTTPException(400, "Invalid status")

        p.status = status
        session.add(p)
        session.commit()
        return RedirectResponse(f"/projects?tab={tab}", status_code=302)


@app.post("/projects/{project_id}/delete")
def project_delete(
    request: Request,
    project_id: int,
    tab: str = Form("active"),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_admin(session, company, user)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        # Cascade delete related rows
        wis = session.exec(select(WorkItem).where(WorkItem.project_id == p.id)).all()
        wi_ids = [w.id for w in wis if w.id is not None]

        if wi_ids:
            for ua in session.exec(select(UnitAllocation).where(UnitAllocation.work_item_id.in_(wi_ids))).all():
                session.delete(ua)
            for sb in session.exec(select(ScheduleBlock).where(ScheduleBlock.work_item_id.in_(wi_ids))).all():
                session.delete(sb)

        for ua in session.exec(select(UnitAllocation).where(UnitAllocation.project_id == p.id)).all():
            session.delete(ua)
        for a in session.exec(select(Allocation).where(Allocation.project_id == p.id)).all():
            session.delete(a)
        for c in session.exec(select(ProjectComment).where(ProjectComment.project_id == p.id)).all():
            session.delete(c)
        for s in session.exec(select(ProjectShare).where(ProjectShare.project_id == p.id)).all():
            session.delete(s)
        for w in wis:
            session.delete(w)

        session.delete(p)
        session.commit()
        return RedirectResponse(f"/projects?tab={tab}", status_code=302)



# -------------------------
# Project unit planning
# -------------------------

@app.get("/projects/{project_id}/units", response_class=HTMLResponse)
def project_units_view(request: Request, project_id: int, view: str = "week", ref: Optional[str] = None, person: Optional[int] = None):
    """Unit-level planning within a project. Create UnitAllocations of WorkItems (units)."""
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        project = session.get(Project, project_id)
        if not project or project.company_id != company.id:
            raise HTTPException(404, "Project not found")

        if int(getattr(project, "budget_minutes", 0) or 0) > 0:
            return RedirectResponse(f"/projects/{project.id}?err=budget_mode_units", status_code=302)
        ref_d = _parse_ref(ref)
        if view in ("day", "week"):
            ref_d = _week_start(ref_d)
        elif view == "month":
            ref_d = _month_start(ref_d)
        periods = _periods(view, ref_d)
        start, end = _date_range_start_end(periods)

        people_all = session.exec(select(Person).where(Person.company_id == company.id)).all()
        selected_person_id: Optional[int] = int(person) if person is not None else None
        people = [p for p in people_all if (selected_person_id is None or p.id == selected_person_id)]
        users = {u.id: u for u in session.exec(select(User)).all()}

        workitems = session.exec(select(WorkItem).where(WorkItem.project_id == project_id)).all()
        # scope quantities by workitem
        ua_all = session.exec(select(UnitAllocation).where(UnitAllocation.project_id == project_id)).all()
        ua_by_wi: Dict[int, int] = {}
        for ua in ua_all:
            ua_by_wi[ua.work_item_id] = ua_by_wi.get(ua.work_item_id, 0) + int(getattr(ua, "minutes", 0) or 0)

        # Build timeline segments per person for allocations within view range
        tmp: Dict[int, List[Tuple[int, int, UnitAllocation]]] = {}
        for ua in ua_all:
            if selected_person_id is not None and ua.person_id != selected_person_id:
                continue
            # Only include those intersecting range
            if ua.end_date < start or ua.start_date > end:
                continue
            s_pi: Optional[int] = None
            e_pi: Optional[int] = None
            for pi, per in enumerate(periods):
                if ua.end_date < per["start"] or ua.start_date > per["end"]:
                    continue
                if s_pi is None:
                    s_pi = pi
                e_pi = pi
            if s_pi is None or e_pi is None:
                continue
            tmp.setdefault(ua.person_id, []).append((s_pi, e_pi, ua))

        # pack into lanes
        timeline_segments: Dict[int, List[Dict]] = {}
        timeline_lanes: Dict[int, int] = {}
        for pid, lst in tmp.items():
            lst.sort(key=lambda t: (t[0], t[1]))
            lane_ends: List[int] = []
            out: List[Dict] = []
            for s_pi, e_pi, ua in lst:
                lane = None
                for i, last_end in enumerate(lane_ends):
                    if last_end < s_pi:
                        lane = i
                        lane_ends[i] = e_pi
                        break
                if lane is None:
                    lane = len(lane_ends)
                    lane_ends.append(e_pi)
                out.append({
                    "id": ua.id,
                    "work_item_id": ua.work_item_id,
                    "person_id": ua.person_id,
                    "start_pi": s_pi,
                    "end_pi": e_pi,
                    "minutes": int(getattr(ua, "minutes", 0) or 0),
                    "start_date": ua.start_date.isoformat(),
                    "end_date": ua.end_date.isoformat(),
                    "lane": lane,
                })
            timeline_segments[pid] = out
            timeline_lanes[pid] = max(1, len(lane_ends))

        # project totals
        totals = _project_scope_planned(session, company.id, project_id)

        return templates.TemplateResponse(
            "project_units.html",
            {
                "request": request,
                "active_user": user,
                "company": company,
                "project": project,
                "people": people,
                "people_all": people_all,
                "users": users,
                "workitems": workitems,
                "workitems_by_id": {wi.id: wi for wi in workitems},
                "ua_by_wi": ua_by_wi,
                "periods": periods,
                "view": view,
                "ref": ref_d.isoformat(),
                "selected_person_id": selected_person_id,
                "timeline_segments": timeline_segments,
                "timeline_lanes": timeline_lanes,
                "totals": totals,
            },
        )


@app.post("/api/unit_allocations")
async def api_unit_alloc_create(request: Request):
    """Create a time-based UnitAllocation.

    Accepts JSON body (preferred) with:
      - project_id, work_item_id, person_id, start_date, end_date
      - minutes OR hours OR (legacy) quantity
      - allow_over (optional)

    This endpoint used to take query parameters; we keep a query fallback for compatibility,
    but the frontend sends JSON.
    """
    try:
        data = await request.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    qp = request.query_params

    def _get(key: str, default=None):
        return data.get(key, qp.get(key, default))

    allow_over_raw = _get("allow_over", False)
    allow_over = str(allow_over_raw).lower() in ("1", "true", "yes", "on")

    project_id = _get("project_id")
    work_item_id = _get("work_item_id")
    person_id = _get("person_id")
    start_date = _get("start_date")
    end_date = _get("end_date")

    if project_id is None or work_item_id is None or person_id is None or not start_date or not end_date:
        raise HTTPException(400, "Missing required fields")

    minutes = _get("minutes")
    hours = _get("hours")
    quantity = _get("quantity")  # legacy

    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")

        project = session.get(Project, int(project_id))
        if not project or project.company_id != company.id:
            raise HTTPException(404, "Project not found")

        # Budget projects do not use unit planning
        if int(getattr(project, "budget_minutes", 0) or 0) > 0:
            raise HTTPException(400, "Projektet är i budgetläge och kan inte planeras med enheter")

        wi = session.get(WorkItem, int(work_item_id))
        if not wi or wi.project_id != project.id:
            raise HTTPException(404, "WorkItem not found")

        person = session.get(Person, int(person_id))
        if not person or person.company_id != company.id:
            raise HTTPException(404, "Person not found")

        # Normalize minutes
        if minutes is None:
            if hours is not None:
                minutes = int(round(float(hours) * 60))
            elif quantity is not None:
                minutes = int(quantity) * int(wi.minutes_per_unit)

        if minutes is None or int(minutes) <= 0:
            raise HTTPException(400, "Missing or invalid minutes")

        # Snap unit planning to a single work-week bucket (Mon–Fri)
        sd_raw = date.fromisoformat(str(start_date))
        sd, ed = _week_bucket(sd_raw)

        ua = UnitAllocation(
            project_id=project.id,
            work_item_id=wi.id,
            person_id=person.id,
            start_date=sd,
            end_date=ed,
            minutes=int(minutes),
        )
        session.add(ua)
        session.flush()

        totals = _project_scope_planned(session, company.id, project.id)
        if totals["planned"] > totals["scope"] and not allow_over:
            session.rollback()
            return JSONResponse(status_code=409, content={"error": "scope_exceeded", "project_id": project.id, **totals})

        session.commit()
        session.refresh(ua)
        return {"ok": True, "id": ua.id}


@app.put("/api/unit_allocations/{ua_id}")
async def api_unit_alloc_update(request: Request, ua_id: int):
    """Update a UnitAllocation. Accepts JSON body (preferred).

    Supports: start_date, end_date, minutes/hours/(legacy quantity), person_id, allow_over.
    """
    try:
        data = await request.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    qp = request.query_params

    def _get(key: str, default=None):
        return data.get(key, qp.get(key, default))

    allow_over_raw = _get("allow_over", False)
    allow_over = str(allow_over_raw).lower() in ("1", "true", "yes", "on")

    start_date = _get("start_date")
    end_date = _get("end_date")
    minutes = _get("minutes")
    hours = _get("hours")
    quantity = _get("quantity")
    person_id = _get("person_id")

    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")

        ua = session.get(UnitAllocation, int(ua_id))
        if not ua:
            raise HTTPException(404, "Not found")

        project = session.get(Project, ua.project_id)
        if not project or project.company_id != company.id:
            raise HTTPException(403, "Forbidden")

        # Budget projects do not use unit planning
        if int(getattr(project, "budget_minutes", 0) or 0) > 0:
            raise HTTPException(400, "Projektet är i budgetläge och kan inte planeras med enheter")

        wi = session.get(WorkItem, ua.work_item_id)

        # Snap dates to a single work-week bucket (Mon–Fri)
        base_d = None
        if start_date:
            base_d = date.fromisoformat(str(start_date))
        elif end_date:
            base_d = date.fromisoformat(str(end_date))
        else:
            base_d = ua.start_date
        sd, ed = _week_bucket(base_d)
        ua.start_date = sd
        ua.end_date = ed

        # Normalize minutes
        if minutes is None:
            if hours is not None:
                minutes = int(round(float(hours) * 60))
            elif quantity is not None and wi is not None:
                minutes = int(quantity) * int(wi.minutes_per_unit)

        if minutes is not None:
            ua.minutes = int(minutes)

        if person_id is not None:
            p = session.get(Person, int(person_id))
            if not p or p.company_id != company.id:
                raise HTTPException(404, "Person not found")
            ua.person_id = p.id

        session.add(ua)
        session.flush()

        totals = _project_scope_planned(session, company.id, ua.project_id)
        if totals["planned"] > totals["scope"] and not allow_over:
            session.rollback()
            return JSONResponse(status_code=409, content={"error": "scope_exceeded", "project_id": ua.project_id, **totals})

        session.commit()
        return {"ok": True}


@app.delete("/api/unit_allocations/{ua_id}")
def api_unit_alloc_delete(request: Request, ua_id: int):
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")
        ua = session.get(UnitAllocation, int(ua_id))
        if not ua:
            raise HTTPException(404, "Not found")
        project = session.get(Project, ua.project_id)
        if not project or project.company_id != company.id:
            raise HTTPException(403, "Forbidden")
        session.delete(ua)
        session.commit()
        return {"ok": True}


@app.post("/projects/{project_id}/rename")
def project_rename(request: Request, project_id: int, name: str = Form(...)):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        _require_planner_or_admin(session, company, user)

        p = session.get(Project, int(project_id))
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        nm = (name or "").strip()
        if not nm:
            return RedirectResponse(f"/projects/{project_id}", status_code=302)
        p.name = nm
        session.add(p)
        session.commit()
        return RedirectResponse(f"/projects/{project_id}?msg=renamed", status_code=302)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        items = session.exec(select(WorkItem).where(WorkItem.project_id == p.id)).all()
        totals = _project_scope_planned(session, company.id, p.id)

        allocs = session.exec(select(Allocation).where(Allocation.project_id == p.id)).all()
        people = {pe.id: pe for pe in session.exec(select(Person).where(Person.company_id == company.id)).all()}
        users = {u.id: u for u in session.exec(select(User)).all()}

        unit_types = session.exec(select(UnitType).where(UnitType.company_id == company.id)).all()

        comments = session.exec(
            select(ProjectComment).where(ProjectComment.project_id == p.id).order_by(ProjectComment.created_at.desc())
        ).all()

        # quick capacity view: next 8 weeks
        today = datetime.now().date()
        periods = _periods("week", today)
        cap_rows = []
        for per in periods:
            cap_h = 0.0
            for a in allocs:
                if a.end_date < per["start"] or a.start_date > per["end"]:
                    continue
                person = people.get(a.person_id)
                if not person:
                    continue
                cap = _capacity_hours_in_range(session, person, per["start"], per["end"])
                cap_h += cap * (a.percent / 100.0)
            cap_rows.append({"label": per["label"], "hours": cap_h})

        err = request.query_params.get("err", "")
        msg = request.query_params.get("msg", "")
        err_msg = ""
        msg_txt = ""
        if err == "budget_has_units":
            err_msg = "Kan inte sätta budget när projektet har enheter. Ta bort alla enheter (och eventuell enhetsplanering) först."
        elif err == "budget_mode_units":
            err_msg = "Projektet är i budgetläge. Enheter/scope och enhetsplanering är avstängt för det här projektet."
        elif err == "budget_invalid":
            err_msg = "Ogiltig budget. Ange antal timmar (t.ex. 120)."
        elif err == "budget_forbidden":
            err_msg = "Du saknar behörighet att ändra budget."
        if msg == "budget_updated":
            msg_txt = "Budget uppdaterad."
        elif msg == "renamed":
            msg_txt = "Projektnamn uppdaterat."

        return templates.TemplateResponse(
            "project_detail.html",
            {
                "request": request,
                "active_user": user,
                "company": company,
                "project": p,
                "items": items,
                "totals": totals,
                "allocs": allocs,
                "people": people,
                "users": users,
                "comments": comments,
                "cap_rows": cap_rows,
                "unit_types": unit_types,
                "can_manage": _is_planner_or_admin(session, company, user),
                "is_admin": _is_admin(session, company, user),
                "err_msg": err_msg,
                "msg": msg_txt,
            },
        )


@app.post("/projects/{project_id}/budget")
def project_set_budget(
    request: Request,
    project_id: int,
    budget_hours: str = Form(""),
):
    """Set/update project budget (in hours). Block switching to budget if the project has unit scope."""
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        if not _is_planner_or_admin(session, company, user):
            return RedirectResponse(f"/projects/{project_id}?err=budget_forbidden", status_code=302)

        p = session.get(Project, int(project_id))
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        raw = str(budget_hours or "").replace(",", ".").strip()
        if raw == "":
            minutes = 0
        else:
            try:
                minutes = int(round(max(0.0, float(raw)) * 60))
            except Exception:
                return RedirectResponse(f"/projects/{p.id}?err=budget_invalid", status_code=302)

        # If switching into budget mode (minutes > 0), require that the project has no unit scope.
        if minutes > 0:
            has_units = session.exec(select(WorkItem).where(WorkItem.project_id == p.id)).first() is not None
            has_unit_plan = session.exec(select(UnitAllocation).where(UnitAllocation.project_id == p.id)).first() is not None
            if has_units or has_unit_plan:
                return RedirectResponse(f"/projects/{p.id}?err=budget_has_units", status_code=302)

        p.budget_minutes = minutes
        session.add(p)
        session.commit()
        return RedirectResponse(f"/projects/{p.id}?msg=budget_updated", status_code=302)

@app.post("/projects/{project_id}/workitems/add")
def project_workitem_add(
    request: Request,
    project_id: int,
    unit_type_id: int = Form(...),
    quantity: int = Form(...),
    minutes_per_unit: str = Form(""),
    title: Optional[str] = Form(None),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_planner_or_admin(session, company, user)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        if int(getattr(p, "budget_minutes", 0) or 0) > 0:
            return RedirectResponse(f"/projects/{p.id}?err=budget_mode_units", status_code=302)

        ut = session.get(UnitType, int(unit_type_id))
        if not ut or ut.company_id != company.id:
            raise HTTPException(404, "UnitType not found")

        qty = int(quantity)
        if qty <= 0:
            return RedirectResponse(f"/projects/{p.id}", status_code=302)

        mpu = int(minutes_per_unit) if str(minutes_per_unit).strip() else int(ut.default_minutes)
        ttl = (title or "").strip() or ut.name

        wi = WorkItem(
            project_id=p.id,
            unit_type_id=ut.id,
            title=ttl,
            quantity=qty,
            minutes_per_unit=mpu,
            total_minutes=qty * mpu,
        )
        session.add(wi)
        session.commit()
        return RedirectResponse(f"/projects/{p.id}", status_code=302)


@app.post("/projects/{project_id}/workitems/{work_item_id}/update")
def project_workitem_update(
    request: Request,
    project_id: int,
    work_item_id: int,
    title: str = Form(...),
    quantity: int = Form(...),
    minutes_per_unit: int = Form(...),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_planner_or_admin(session, company, user)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        if int(getattr(p, "budget_minutes", 0) or 0) > 0:
            return RedirectResponse(f"/projects/{p.id}?err=budget_mode_units", status_code=302)

        wi = session.get(WorkItem, work_item_id)
        if not wi or wi.project_id != p.id:
            raise HTTPException(404, "WorkItem not found")

        wi.title = (title or "").strip() or wi.title
        wi.quantity = int(quantity)
        wi.minutes_per_unit = int(minutes_per_unit)
        wi.total_minutes = wi.quantity * wi.minutes_per_unit
        session.add(wi)
        session.commit()
        return RedirectResponse(f"/projects/{p.id}", status_code=302)


@app.post("/projects/{project_id}/workitems/{work_item_id}/delete")
def project_workitem_delete(
    request: Request,
    project_id: int,
    work_item_id: int,
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        _require_planner_or_admin(session, company, user)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        if int(getattr(p, "budget_minutes", 0) or 0) > 0:
            return RedirectResponse(f"/projects/{p.id}?err=budget_mode_units", status_code=302)

        wi = session.get(WorkItem, work_item_id)
        if not wi or wi.project_id != p.id:
            raise HTTPException(404, "WorkItem not found")

        # delete dependent unit allocations + schedule blocks
        for ua in session.exec(select(UnitAllocation).where(UnitAllocation.work_item_id == wi.id)).all():
            session.delete(ua)
        for sb in session.exec(select(ScheduleBlock).where(ScheduleBlock.work_item_id == wi.id)).all():
            session.delete(sb)

        session.delete(wi)
        session.commit()
        return RedirectResponse(f"/projects/{p.id}", status_code=302)



@app.post("/projects/{project_id}/comment")
def project_add_comment(
    request: Request,
    project_id: int,
    body: str = Form(...),
):
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")

        session.add(ProjectComment(project_id=p.id, author_user_id=user.id, body=body.strip()))
        session.commit()
        return RedirectResponse(f"/projects/{p.id}", status_code=302)


@app.post("/projects/{project_id}/share")
def project_create_share(
    request: Request,
    project_id: int,
    email: str = Form(...),
    permission: str = Form("read"),
    from_dt: str = Form(...),
    to_dt: str = Form(...),
):
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)
        p = session.get(Project, project_id)
        if not p or p.company_id != company.id:
            raise HTTPException(404, "Project not found")
        token = secrets.token_urlsafe(16)
        share = ProjectShare(
            project_id=p.id,
            token=token,
            email=email.strip(),
            permission=permission,
            from_dt=datetime.fromisoformat(from_dt),
            to_dt=datetime.fromisoformat(to_dt),
            work_item_ids_csv="",
        )
        session.add(share)
        session.commit()
        return RedirectResponse(f"/projects/{p.id}", status_code=302)


# -------------------------
# Portfolio / Resource view (allocations)
# -------------------------

@app.get("/", response_class=HTMLResponse)
def portfolio_view(request: Request, view: str = "week", ref: Optional[str] = None, person: Optional[int] = None):
    """Tidschema/portfölj: hög-nivå % per projekt + ad-hoc % + (tid)-planering av enheter."""
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        ref_d = _parse_ref(ref)

        # Normalize reference date:
        # - day/week always start on nearest Monday
        # - month snaps to first of month
        if view in ("day", "week"):
            ref_d = _week_start(ref_d)
        elif view == "month":
            ref_d = _month_start(ref_d)

        periods = _periods(view, ref_d)
        start, end = _date_range_start_end(periods)

        # People list and optional person filter (?person=<id>) for ALL views
        people_all = session.exec(select(Person).where(Person.company_id == company.id)).all()
        selected_person_id: Optional[int] = int(person) if person is not None else None
        if selected_person_id is not None:
            people = [p for p in people_all if p.id == selected_person_id]
        else:
            people = people_all
        users = {u.id: u for u in session.exec(select(User)).all()}

        # Only active projects in tidschema
        projects = session.exec(select(Project).where(Project.company_id == company.id, Project.status == "active")).all()
        projects_by_id = {p.id: p for p in projects}
        proj_totals = {p.id: _project_scope_planned(session, company.id, p.id) for p in projects}
        active_project_ids = set(projects_by_id.keys())

        # Allocations (% per project)
        allocs_all = _allocations_in_range(session, company.id, start, end)
        allocs = [a for a in allocs_all if a.project_id in active_project_ids]

        # Ad-hoc allocations (% without project)
        adhoc_all = session.exec(
            select(AdhocAllocation).where(
                AdhocAllocation.company_id == company.id,
                AdhocAllocation.end_date >= start,
                AdhocAllocation.start_date <= end,
            )
        ).all()

        person_ids = [p.id for p in people]

        # Time off markers (visual only)
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
        off_cells: Dict[Tuple[int, int], bool] = {}
        if person_ids:
            timeoffs = session.exec(
                select(TimeOff).where(TimeOff.person_id.in_(person_ids), TimeOff.end > start_dt, TimeOff.start < end_dt)
            ).all()
            for o in timeoffs:
                for pi, per in enumerate(periods):
                    if o.end.date() < per["start"] or o.start.date() > per["end"]:
                        continue
                    off_cells[(o.person_id, pi)] = True

        # Unit allocations (time-based minutes) for active projects
        unit_allocs = []
        if person_ids and active_project_ids:
            unit_allocs = session.exec(
                select(UnitAllocation).where(
                    UnitAllocation.person_id.in_(person_ids),
                    UnitAllocation.project_id.in_(list(active_project_ids)),
                    UnitAllocation.end_date >= start,
                    UnitAllocation.start_date <= end,
                )
            ).all()

        # WorkItem maps for unit allocations + sidebar expansion
        workitems = []
        if active_project_ids:
            workitems = session.exec(select(WorkItem).where(WorkItem.project_id.in_(list(active_project_ids)))).all()
        wi_by_id = {w.id: w for w in workitems}

        # Per-workitem planned minutes (for sidebar)
        wi_planned: Dict[int, int] = {}
        for ua in unit_allocs:
            wi_planned[ua.work_item_id] = wi_planned.get(ua.work_item_id, 0) + int(getattr(ua, "minutes", 0) or 0)

        proj_items: Dict[int, list] = {}
        for wi in workitems:
            planned_m = wi_planned.get(wi.id, 0)
            proj_items.setdefault(wi.project_id, []).append(
                {
                    "id": wi.id,
                    "title": wi.title,
                    "budget": int(wi.total_minutes),
                    "planned": int(planned_m),
                    "remaining": int(wi.total_minutes - planned_m),
                    "project_id": wi.project_id,
                }
            )
        for pid in proj_items:
            proj_items[pid].sort(key=lambda x: x["title"].lower())

        # Helpers for per-cell overlap
        def _overlap_days(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
            lo = max(a_start, b_start)
            hi = min(a_end, b_end)
            if hi < lo:
                return 0
            return (hi - lo).days + 1

        # Capacity cache (minutes) per person+period
        cap_min: Dict[Tuple[int, int], int] = {}
        for pe in people:
            for pi, per in enumerate(periods):
                cap_h = _capacity_hours_in_range(session, pe, per["start"], per["end"])
                cap_min[(pe.id, pi)] = int(round(cap_h * 60))

        # Percent allocations per cell
        cell_allocs: Dict[Tuple[int, int], List[Allocation]] = {}
        for a in allocs:
            for pi, per in enumerate(periods):
                if a.end_date < per["start"] or a.start_date > per["end"]:
                    continue
                cell_allocs.setdefault((a.person_id, pi), []).append(a)

        cell_adhoc: Dict[Tuple[int, int], List[AdhocAllocation]] = {}
        for a in adhoc_all:
            if person_ids and a.person_id not in person_ids:
                continue
            for pi, per in enumerate(periods):
                if a.end_date < per["start"] or a.start_date > per["end"]:
                    continue
                cell_adhoc.setdefault((a.person_id, pi), []).append(a)

        # Unit minutes per cell (distribute across days)
        cell_unit_tags: Dict[Tuple[int, int], List[Dict]] = {}
        cell_unit_minutes: Dict[Tuple[int, int], int] = {}
        for ua in unit_allocs:
            ua_min = int(getattr(ua, "minutes", 0) or 0)
            if ua_min <= 0:
                continue
            days_total = (ua.end_date - ua.start_date).days + 1
            if days_total <= 0:
                continue
            # minutes per day, distributed
            mpd = ua_min / float(days_total)
            wi = wi_by_id.get(ua.work_item_id)
            wi_title = wi.title if wi else f"WorkItem {ua.work_item_id}"
            for pi, per in enumerate(periods):
                od = _overlap_days(ua.start_date, ua.end_date, per["start"], per["end"])
                if od <= 0:
                    continue
                m_here = int(round(mpd * od))
                if m_here <= 0:
                    continue
                key = (ua.person_id, pi)
                cell_unit_minutes[key] = cell_unit_minutes.get(key, 0) + m_here
                cell_unit_tags.setdefault(key, []).append(
                    {
                        "id": ua.id,
                        "project_id": ua.project_id,
                        "work_item_id": ua.work_item_id,
                        "title": wi_title,
                        "minutes": m_here,           # minutes shown in this cell
                        "ua_minutes": ua_min,        # total minutes for this allocation (for +/- 1h edits)
                        "ua_start": ua.start_date.isoformat(),
                        "ua_end": ua.end_date.isoformat(),
                    }
                )

        # For each person row, compute how many unit-tags are shown at most in a single cell.
        # Used to reserve row height so tags never end up hidden behind other layers.
        timeline_unitlines: Dict[int, int] = {}
        for pe in people:
            mx = 0
            for pi in range(len(periods)):
                mx = max(mx, len(cell_unit_tags.get((pe.id, pi), [])))
            timeline_unitlines[pe.id] = mx

        # Timeline segments: combine project allocations + adhoc allocations (bars)
        timeline_segments: Dict[int, List[Dict]] = {}
        timeline_lanes: Dict[int, int] = {}
        if view in ("day", "week", "month"):
            tmp: Dict[int, List[Tuple[int, int, Dict]]] = {}

            def _add_seg(pid: int, s_pi: int, e_pi: int, d: Dict):
                tmp.setdefault(pid, []).append((s_pi, e_pi, d))

            for a in allocs:
                if person_ids and a.person_id not in person_ids:
                    continue
                s_pi = None
                e_pi = None
                for pi, per in enumerate(periods):
                    if a.end_date < per["start"] or a.start_date > per["end"]:
                        continue
                    if s_pi is None:
                        s_pi = pi
                    e_pi = pi
                if s_pi is None or e_pi is None:
                    continue
                _add_seg(
                    a.person_id,
                    s_pi,
                    e_pi,
                    {
                        "type": "alloc",
                        "id": a.id,
                        "project_id": a.project_id,
                        "percent": a.percent,
                        "start_date": a.start_date.isoformat(),
                        "end_date": a.end_date.isoformat(),
                    },
                )

            for a in adhoc_all:
                if person_ids and a.person_id not in person_ids:
                    continue
                s_pi = None
                e_pi = None
                for pi, per in enumerate(periods):
                    if a.end_date < per["start"] or a.start_date > per["end"]:
                        continue
                    if s_pi is None:
                        s_pi = pi
                    e_pi = pi
                if s_pi is None or e_pi is None:
                    continue
                _add_seg(
                    a.person_id,
                    s_pi,
                    e_pi,
                    {
                        "type": "adhoc",
                        "id": a.id,
                        "title": a.title,
                        "color": a.color,
                        "percent": a.percent,
                        "start_date": a.start_date.isoformat(),
                        "end_date": a.end_date.isoformat(),
                    },
                )

            for pid, lst in tmp.items():
                lst.sort(key=lambda t: (t[0], t[1]))
                lane_ends: List[int] = []
                out: List[Dict] = []
                for s_pi, e_pi, d in lst:
                    lane = None
                    for i, last_end in enumerate(lane_ends):
                        if last_end < s_pi:
                            lane = i
                            lane_ends[i] = e_pi
                            break
                    if lane is None:
                        lane = len(lane_ends)
                        lane_ends.append(e_pi)
                    d2 = dict(d)
                    d2.update({"start_pi": s_pi, "end_pi": e_pi, "lane": lane})
                    out.append(d2)
                timeline_segments[pid] = out
                timeline_lanes[pid] = max(1, len(lane_ends))

        # Sum % per cell (project + adhoc + unit->% via capacity)
        cell_sum: Dict[Tuple[int, int], int] = {}
        row_sum: Dict[int, int] = {}
        for pe in people:
            total = 0
            for pi in range(len(periods)):
                pct = 0
                pct += sum(a.percent for a in cell_allocs.get((pe.id, pi), []))
                pct += sum(a.percent for a in cell_adhoc.get((pe.id, pi), []))
                # unit minutes -> percent
                cm = cap_min.get((pe.id, pi), 0)
                um = cell_unit_minutes.get((pe.id, pi), 0)
                if cm > 0 and um > 0:
                    pct += int(round((um / float(cm)) * 100.0))
                cell_sum[(pe.id, pi)] = pct
                total += pct
            row_sum[pe.id] = total

        denom = max(1, len(periods))
        row_avg: Dict[int, int] = {pid: int(round(total / denom)) for pid, total in row_sum.items()}
        row_peak: Dict[int, int] = {}
        for pe in people:
            mx = 0
            for pi in range(len(periods)):
                mx = max(mx, cell_sum.get((pe.id, pi), 0))
            row_peak[pe.id] = mx

        # navigation
        if view == "month":
            prev_ref = (_month_start(ref_d) - timedelta(days=1)).replace(day=1)
            next_ref = (_month_end(ref_d) + timedelta(days=1)).replace(day=1)
        elif view == "day":
            prev_ref = ref_d - timedelta(days=7)
            next_ref = ref_d + timedelta(days=7)
        elif view == "week":
            prev_ref = ref_d - timedelta(days=35)
            next_ref = ref_d + timedelta(days=35)
        else:
            prev_ref = ref_d - timedelta(days=7)
            next_ref = ref_d + timedelta(days=7)

        td = date.today()
        if view in ("day", "week"):
            today_ref = _week_start(td)
        else:
            today_ref = _month_start(td)

        return templates.TemplateResponse(
            "portfolio.html",
            {
                "request": request,
                "active_user": user,
                "company": company,
                "view": view,
                "ref": ref_d,
                "selected_person_id": selected_person_id,
                "prev_ref": prev_ref.isoformat(),
                "next_ref": next_ref.isoformat(),
                "today_ref": today_ref.isoformat(),
                "periods": periods,
                "people": people,
                "people_all": people_all,
                "users": users,
                "projects": projects,
                "projects_by_id": projects_by_id,
                "proj_totals": proj_totals,
                "proj_items": proj_items,
                "cell_sum": cell_sum,
                "row_avg": row_avg,
                "row_peak": row_peak,
                "off_cells": off_cells,
                "timeline_segments": timeline_segments,
                "timeline_lanes": timeline_lanes,
                "timeline_unitlines": timeline_unitlines,
                "cell_unit_tags": cell_unit_tags,
            },
        )




# -------------------------
# Report (30 weeks)
# -------------------------

@app.get("/report", response_class=HTMLResponse)
def report_view(request: Request, ref: Optional[str] = None):
    """Rapport: visa 30 veckor samtidigt."""
    with get_session() as session:
        user = _get_active_user(session, request)
        company = _get_active_company(session, request)
        if not company:
            return RedirectResponse("/setup", status_code=302)

        ref_d = _parse_ref(ref)
        ref_d = _week_start(ref_d)

        # Build 30 week periods (Mon-Fri)
        periods = []
        for i in range(30):
            s = ref_d + timedelta(days=7 * i)
            e = s + timedelta(days=4)
            iso = s.isocalendar()
            periods.append({
                "i": i,
                "label": f"v{iso.week}",
                "start": s,
                "end": e,
                "start_iso": s.isoformat(),
                "end_iso": e.isoformat(),
            })

        people = session.exec(select(Person).where(Person.company_id == company.id)).all()
        users = {u.id: u for u in session.exec(select(User)).all()}

        projects = session.exec(select(Project).where(Project.company_id == company.id, Project.status == "active")).all()
        active_project_ids = {p.id for p in projects}

        # Load allocations overlapping full 30-week range
        start = periods[0]["start"]
        end = periods[-1]["end"]

        allocs = session.exec(
            select(Allocation).where(
                Allocation.company_id == company.id,
                Allocation.project_id.in_(list(active_project_ids) or [-1]),
                Allocation.end_date >= start,
                Allocation.start_date <= end,
            )
        ).all()

        adhoc = session.exec(
            select(AdhocAllocation).where(
                AdhocAllocation.company_id == company.id,
                AdhocAllocation.end_date >= start,
                AdhocAllocation.start_date <= end,
            )
        ).all()

        unit_allocs = session.exec(
            select(UnitAllocation).where(
                UnitAllocation.project_id.in_(list(active_project_ids) or [-1]),
                UnitAllocation.end_date >= start,
                UnitAllocation.start_date <= end,
            )
        ).all()

        def overlap_days(a_start, a_end, b_start, b_end):
            lo = max(a_start, b_start)
            hi = min(a_end, b_end)
            if hi < lo:
                return 0
            return (hi - lo).days + 1

        # Color banding for report cells.
        # Requested buckets: 0–20, 20–40, 40–60, 80–100, >100.
        # We map 60–80 to the same band as 40–60 to keep 5 colors.
        def util_band(pct: int) -> str:
            if pct < 20:
                return "b0"
            if pct < 40:
                return "b20"
            if pct < 80:
                return "b40"
            if pct <= 100:
                return "b80"
            return "b100"

        # Capacity cache
        cap_min = {}
        for pe in people:
            for pi, per in enumerate(periods):
                cap_h = _capacity_hours_in_range(session, pe, per["start"], per["end"])
                cap_min[(pe.id, pi)] = int(round(cap_h * 60))

        # Planned minutes per person/week
        cell = {}

        # % allocations (project + adhoc)
        for a in allocs:
            for pi, per in enumerate(periods):
                if a.end_date < per["start"] or a.start_date > per["end"]:
                    continue
                cm = cap_min.get((a.person_id, pi), 0)
                if cm <= 0:
                    continue
                cell[(a.person_id, pi)] = cell.get((a.person_id, pi), 0) + int(round(cm * (a.percent / 100.0)))

        for a in adhoc:
            for pi, per in enumerate(periods):
                if a.end_date < per["start"] or a.start_date > per["end"]:
                    continue
                cm = cap_min.get((a.person_id, pi), 0)
                if cm <= 0:
                    continue
                cell[(a.person_id, pi)] = cell.get((a.person_id, pi), 0) + int(round(cm * (a.percent / 100.0)))

        # Unit allocations distributed by days
        for ua in unit_allocs:
            ua_min = int(getattr(ua, "minutes", 0) or 0)
            if ua_min <= 0:
                continue
            days_total = (ua.end_date - ua.start_date).days + 1
            if days_total <= 0:
                continue
            mpd = ua_min / float(days_total)
            for pi, per in enumerate(periods):
                od = overlap_days(ua.start_date, ua.end_date, per["start"], per["end"])
                if od <= 0:
                    continue
                cell[(ua.person_id, pi)] = cell.get((ua.person_id, pi), 0) + int(round(mpd * od))

        # Build rows
        rows = []
        for pe in people:
            u = users.get(pe.user_id)
            name = u.name if u else f"User {pe.user_id}"
            per_cells = []
            for pi, per in enumerate(periods):
                cm = cap_min.get((pe.id, pi), 0)
                pm = cell.get((pe.id, pi), 0)
                pct = int(round((pm / float(cm)) * 100.0)) if cm > 0 else 0
                per_cells.append({
                    "pct": pct,
                    "hours": round((pm or 0) / 60.0, 1),
                    "cap_hours": round((cm or 0) / 60.0, 1),
                    "band": util_band(pct),
                })
            rows.append({"person_id": pe.id, "name": name, "cells": per_cells})

        
        # Navigation refs (shift by 30 weeks)
        today_ref = _week_start(date.today())
        prev_ref = (ref_d - timedelta(days=7 * 30))
        next_ref = (ref_d + timedelta(days=7 * 30))

        return templates.TemplateResponse(
            "report.html",
            {
                "request": request,
                "active_user": user,
                "company": company,
                "ref": ref_d,
                "prev_ref": prev_ref.isoformat(),
                "next_ref": next_ref.isoformat(),
                "today_ref": today_ref.isoformat(),
                "periods": periods,
                "rows": rows,
            },
        )
# -------------------------
# Allocations API (JSON)
# -------------------------

@app.post("/api/allocations")
async def api_alloc_create(request: Request):
    data = await request.json()
    allow_over = bool(data.get("allow_over", False))
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")

        project_id = int(data["project_id"])
        person_id = int(data["person_id"])

        project = session.get(Project, project_id)
        if not project or project.company_id != company.id:
            raise HTTPException(404, "Project not found")

        alloc = Allocation(
            company_id=company.id,
            project_id=project_id,
            person_id=person_id,
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            percent=int(data["percent"]),
        )
        session.add(alloc)
        session.flush()

        totals = _project_scope_planned(session, company.id, project_id)
        if totals["planned"] > totals["scope"] and not allow_over:
            session.rollback()
            return JSONResponse(status_code=409, content={"error": "scope_exceeded", "project_id": project_id, **totals})

        session.commit()
        session.refresh(alloc)
        return {"ok": True, "id": alloc.id}


@app.put("/api/allocations/{alloc_id}")
async def api_alloc_update(request: Request, alloc_id: int):
    data = await request.json()
    allow_over = bool(data.get("allow_over", False))
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")

        alloc = session.get(Allocation, alloc_id)
        if not alloc:
            raise HTTPException(404, "Allocation not found")
        # basic company check via project
        project = session.get(Project, alloc.project_id)
        if not project or project.company_id != company.id:
            raise HTTPException(404, "Project not found")

        if "person_id" in data:
            alloc.person_id = int(data["person_id"])
        if "start_date" in data:
            alloc.start_date = date.fromisoformat(data["start_date"])
        if "end_date" in data:
            alloc.end_date = date.fromisoformat(data["end_date"])
        if "percent" in data:
            alloc.percent = int(data["percent"])

        session.add(alloc)
        session.flush()

        totals = _project_scope_planned(session, company.id, alloc.project_id)
        if totals["planned"] > totals["scope"] and not allow_over:
            session.rollback()
            return JSONResponse(status_code=409, content={"error": "scope_exceeded", "project_id": alloc.project_id, **totals})

        session.commit()
        return {"ok": True}


@app.delete("/api/allocations/{alloc_id}")
def api_alloc_delete(alloc_id: int):
    with get_session() as session:
        alloc = session.get(Allocation, alloc_id)
        if not alloc:
            raise HTTPException(404, "Allocation not found")
        session.delete(alloc)
        session.commit()
        return {"ok": True}

# -------------------------
# Ad-hoc allocations API (JSON)
# -------------------------

@app.post("/api/adhoc_allocations")
async def api_adhoc_create(request: Request):
    data = await request.json()
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")
        person_id = int(data["person_id"])
        person = session.get(Person, person_id)
        if not person or person.company_id != company.id:
            raise HTTPException(404, "Person not found")

        a = AdhocAllocation(
            company_id=company.id,
            person_id=person_id,
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            percent=int(data.get("percent", 0)),
            title=str(data.get("title", "")).strip() or "Fri text",
            color=str(data.get("color", "#ff4fa3")),
        )
        session.add(a)
        session.commit()
        session.refresh(a)
        return {"ok": True, "id": a.id}


@app.put("/api/adhoc_allocations/{adhoc_id}")
async def api_adhoc_update(request: Request, adhoc_id: int):
    data = await request.json()
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")
        a = session.get(AdhocAllocation, adhoc_id)
        if not a or a.company_id != company.id:
            raise HTTPException(404, "Not found")

        if "person_id" in data and data["person_id"] is not None:
            person_id = int(data["person_id"])
            person = session.get(Person, person_id)
            if not person or person.company_id != company.id:
                raise HTTPException(404, "Person not found")
            a.person_id = person_id
        if "start_date" in data and data["start_date"]:
            a.start_date = date.fromisoformat(data["start_date"])
        if "end_date" in data and data["end_date"]:
            a.end_date = date.fromisoformat(data["end_date"])
        if "percent" in data and data["percent"] is not None:
            a.percent = int(data["percent"])
        if "title" in data and data["title"] is not None:
            a.title = str(data["title"]).strip() or a.title
        if "color" in data and data["color"] is not None:
            a.color = str(data["color"]).strip() or a.color

        session.add(a)
        session.commit()
        return {"ok": True}


@app.delete("/api/adhoc_allocations/{adhoc_id}")
def api_adhoc_delete(request: Request, adhoc_id: int):
    with get_session() as session:
        company = _get_active_company(session, request)
        if not company:
            raise HTTPException(400, "No company")
        a = session.get(AdhocAllocation, adhoc_id)
        if not a or a.company_id != company.id:
            raise HTTPException(404, "Not found")
        session.delete(a)
        session.commit()
        return {"ok": True}



# -------------------------
# External sharing
# -------------------------

@app.get("/shared/{token}", response_class=HTMLResponse)
def shared_view(request: Request, token: str):
    with get_session() as session:
        share = session.exec(select(ProjectShare).where(ProjectShare.token == token)).first()
        if not share:
            raise HTTPException(404, "Share not found")
        p = session.get(Project, share.project_id)
        if not p:
            raise HTTPException(404, "Project not found")
        company = session.get(Company, p.company_id)
        items = session.exec(select(WorkItem).where(WorkItem.project_id == p.id)).all()
        totals = _project_scope_planned(session, company.id, p.id)

        comments = session.exec(
            select(ProjectComment).where(ProjectComment.project_id == p.id).order_by(ProjectComment.created_at.desc())
        ).all()

        start = share.from_dt.date()
        end = share.to_dt.date()
        allocs = session.exec(
            select(Allocation).where(Allocation.project_id == p.id, Allocation.end_date >= start, Allocation.start_date <= end)
        ).all()
        people = {pe.id: pe for pe in session.exec(select(Person).where(Person.company_id == p.company_id)).all()}
        users = {u.id: u for u in session.exec(select(User)).all()}

        return templates.TemplateResponse(
            "shared.html",
            {
                "request": request,
                "share": share,
                "project": p,
                "company": company,
                "items": items,
                "totals": totals,
                "comments": comments,
                "allocs": allocs,
                "people": people,
                "users": users,
            },
        )


@app.post("/shared/{token}/comment")
def shared_add_comment(token: str, body: str = Form(...)):
    with get_session() as session:
        share = session.exec(select(ProjectShare).where(ProjectShare.token == token)).first()
        if not share:
            raise HTTPException(404, "Share not found")
        if share.permission != "comment":
            raise HTTPException(403, "Sharing permission does not allow comments")
        session.add(ProjectComment(project_id=share.project_id, author_external_email=share.email, body=body.strip()))
        session.commit()
        return RedirectResponse(f"/shared/{token}", status_code=302)
