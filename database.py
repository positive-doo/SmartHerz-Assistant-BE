"""Shared SQL Server connection settings for the backend and operator commands."""
import os
from contextlib import contextmanager

import pyodbc

MSSQL_DRIVER = os.getenv("MSSQL_DRIVER") or "ODBC Driver 18 for SQL Server"
MSSQL_ENCRYPT = os.getenv("MSSQL_ENCRYPT") or "yes"
MSSQL_TRUST_SERVER_CERTIFICATE = os.getenv("MSSQL_TRUST_SERVER_CERTIFICATE") or "yes"


@contextmanager
def sql_connection():
    host = os.getenv("MSSQL_HOST")
    database = os.getenv("MSSQL_DB")
    user = os.getenv("MSSQL_USER")
    password = os.getenv("MSSQL_PASS")
    if not all(value and value.strip() for value in (host, database, user, password)):
        raise RuntimeError("SQL database is not configured.")

    connection = pyodbc.connect(
        driver=f"{{{MSSQL_DRIVER}}}",
        server=host,
        database=database,
        uid=user,
        pwd=password,
        Encrypt=MSSQL_ENCRYPT,
        TrustServerCertificate=MSSQL_TRUST_SERVER_CERTIFICATE,
        timeout=10,
    )
    try:
        yield connection
    finally:
        connection.close()
