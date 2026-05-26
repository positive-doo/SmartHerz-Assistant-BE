import logging
import os
import sys
from time import perf_counter

import pyodbc
from fastapi import FastAPI, Request
from openai import OpenAI
from pinecone import Pinecone
from pydantic import BaseModel, Field


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
for logger_name in ("httpx", "httpcore", "pinecone"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER") or "ODBC Driver 18 for SQL Server"
MSSQL_ENCRYPT = os.getenv("MSSQL_ENCRYPT") or "yes"
MSSQL_TRUST_SERVER_CERTIFICATE = os.getenv("MSSQL_TRUST_SERVER_CERTIFICATE") or "yes"


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    status: str
    query: str
    response: str


class HealthResponse(BaseModel):
    status: str


class StatusResponse(BaseModel):
    backend: str
    sql_database: str
    vector_database: str
    openai_api: str
    environment: str


app = FastAPI(
    title="SmartHerz Assistant Backend",
    version="0.1.0",
)


def _configured(*values: str | None) -> bool:
    return all(value and value.strip() for value in values)


def check_sql_database() -> str:
    host = os.getenv("MSSQL_HOST")
    database = os.getenv("MSSQL_DB")
    user = os.getenv("MSSQL_USER")
    password = os.getenv("MSSQL_PASS")

    if not _configured(host, database, user, password):
        return "not_configured"

    connection = None
    cursor = None
    try:
        connection = pyodbc.connect(
            driver=f"{{{MSSQL_DRIVER}}}",
            server=host,
            database=database,
            uid=user,
            pwd=password,
            Encrypt=MSSQL_ENCRYPT,
            TrustServerCertificate=MSSQL_TRUST_SERVER_CERTIFICATE,
        )
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        return "ok"
    except Exception as exc:
        logger.warning("SQL status check failed: %s", exc)
        return "error"
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def check_vector_database() -> str:
    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    pinecone_index = os.getenv("PINECONE_INDEX")

    if not _configured(pinecone_api_key):
        return "not_configured"

    try:
        pc = Pinecone(api_key=pinecone_api_key)
        if pinecone_index:
            pc.describe_index(pinecone_index)
        else:
            pc.list_indexes()
        return "ok"
    except Exception as exc:
        logger.warning("Pinecone status check failed: %s", exc)
        return "error"


def check_openai_api() -> str:
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not _configured(openai_api_key):
        return "not_configured"

    try:
        client = OpenAI(api_key=openai_api_key, timeout=5.0)
        client.models.list()
        return "ok"
    except Exception as exc:
        logger.warning("OpenAI status check failed: %s", exc)
        return "error"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = perf_counter()
    response = await call_next(request)
    duration_ms = round((perf_counter() - started) * 1000, 2)
    logging.getLogger("app.request").info(
        "request completed method=%s path=%s status_code=%s duration_ms=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/healthz", response_model=HealthResponse)
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status", response_model=StatusResponse)
def api_status() -> dict[str, str]:
    return {
        "backend": "ok",
        "sql_database": check_sql_database(),
        "vector_database": check_vector_database(),
        "openai_api": check_openai_api(),
        "environment": ENVIRONMENT,
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> dict[str, str]:
    return {
        "status": "ok",
        "query": payload.query,
        "response": "AI pipeline operational",
    }
