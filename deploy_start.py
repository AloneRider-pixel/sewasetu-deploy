"""Single-container entrypoint (free hosting): embedded Postgres, SMS simulator, nightly deemed-approval
job, and the gateway (portal + both MCP servers + eval dashboard) on one HTTPS port."""
import gzip, lzma, os, subprocess, sys, threading, time, shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = os.environ.get("PORT", "7860")
DATA = os.environ.get("PGDATA_DIR", "/tmp/pgdata")
host = os.environ.get("SPACE_HOST")
if host and not os.environ.get("PUBLIC_BASE_URL"):
    os.environ["PUBLIC_BASE_URL"] = "https://" + host
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:" + PORT)
assert os.environ.get("ADMIN_PASSWORD"), "ADMIN_PASSWORD must be set (Space secret)"

import pgserver
srv = pgserver.get_server(DATA, cleanup_mode=None)
bin_dir = os.path.join(os.path.dirname(pgserver.__file__), "pginstall", "bin")
psql = os.path.join(bin_dir, "psql")
sock = DATA


def run_sql(db, user, sql=None, file=None, stdin=None):
    cmd = [psql, "-h", sock, "-U", user, "-d", db, "-q", "-v", "ON_ERROR_STOP=1"] + (["-c", sql] if sql else ["-f", file] if file else [])
    return subprocess.run(cmd, input=stdin, capture_output=True, text=isinstance(stdin, str), env={**os.environ, "PGPASSWORD": "sewasetu123"})


have = subprocess.run([psql, "-h", sock, "-U", "postgres", "-tAc", "SELECT 1 FROM pg_database WHERE datname='sewasetu'"], capture_output=True, text=True).stdout.strip()
if not have:
    print("first boot: creating database and loading seed", flush=True)
    for s in ("CREATE ROLE sewasetu LOGIN SUPERUSER PASSWORD 'sewasetu123'", "CREATE DATABASE sewasetu OWNER sewasetu"):
        subprocess.run([psql, "-h", sock, "-U", "postgres", "-c", s], check=True)
    r = run_sql("sewasetu", "sewasetu", file=os.path.join(ROOT, "app", "schema.sql")); assert r.returncode == 0, r.stderr
    seed_xz, seed = os.path.join(ROOT, "seed", "seed.sql.xz"), os.path.join(ROOT, "seed", "seed.sql")
    data = lzma.open(seed_xz, "rb").read() if os.path.exists(seed_xz) else open(seed, "rb").read()
    r = subprocess.run([psql, "-h", sock, "-U", "sewasetu", "-d", "sewasetu", "-q", "-v", "ON_ERROR_STOP=1"], input=data, capture_output=True,
                       env={**os.environ, "PGPASSWORD": "sewasetu123"})
    assert r.returncode == 0, r.stderr[-500:]
    print("seed loaded", flush=True)

env = {**os.environ, "DB_HOST": sock, "DB_USER": "sewasetu", "DB_PASSWORD": "sewasetu123", "DB_NAME": "sewasetu",
       "SMS_GATEWAY_URL": "http://127.0.0.1:8025", "UPLOAD_DIR": "/tmp/uploads", "LOG_DIR": "/tmp/logs"}
os.environ.update(env)
subprocess.Popen([sys.executable, os.path.join(ROOT, "smsgw", "app.py")], env=env)
time.sleep(1)
job = os.path.join(ROOT, "app", "scripts", "deemed_approval.py")
subprocess.run([sys.executable, job], env=env)


def nightly():
    IST = ZoneInfo("Asia/Kolkata")
    while True:
        n = datetime.now(IST)
        nxt = n.replace(hour=2, minute=0, second=0, microsecond=0)
        if nxt <= n:
            nxt += timedelta(days=1)
        time.sleep((nxt - n).total_seconds())
        subprocess.run([sys.executable, job], env=env)


threading.Thread(target=nightly, daemon=True).start()
os.chdir(ROOT)
os.execvpe(sys.executable, [sys.executable, "-m", "uvicorn", "agents.gateway:app", "--host", "0.0.0.0", "--port", PORT, "--proxy-headers", "--forwarded-allow-ips", "*"], env)
