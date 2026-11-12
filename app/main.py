"""FastAPI app: startup guard and routes (PRD 4.4, 12). Route handlers are plain def (PRD 4.4)."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import get_settings, load_env_file

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_file()
    get_settings().validate()  # raises ConfigError: the app never serves with a non-sandbox host (INV-12)
    conn = db.connect()
    try:
        if not db.schema_exists(conn):
            db.init_schema(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="SynapsePay", lifespan=lifespan)


@app.get("/api/health")
def health():
    s = get_settings()
    conn = db.connect()
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    return {"ok": True, "run_id": meta.get("run_id"), "scenario": meta.get("scenario"),
            "paypal_mode": s.paypal_mode, "llm_mode": s.llm_mode}


if STATIC_DIR.is_dir():  # B4 adds app/static; GET / serves the operator page
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
