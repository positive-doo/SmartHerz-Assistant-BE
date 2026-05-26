# SmartHerz Assistant Backend

Minimal FastAPI backend for the SmartHerz chatbot demo. Swagger is available
at `/docs` when the service is running.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check. |
| `GET` | `/api/status` | Checks backend, SQL Server, Pinecone, and optional OpenAI API connectivity. |
| `POST` | `/api/chat` | Minimal chatbot pipeline validation endpoint. |

## Environment

Set these variables in Azure Container Apps. For local development, the same
variables can be exported in the shell before starting Uvicorn.

| Variable | Required | Description |
| --- | --- | --- |
| `ENVIRONMENT` | No | Runtime environment label. Default: `development`. |
| `PORT` | No | Uvicorn port. Default: `8000`. |
| `MSSQL_HOST` | For SQL check | SQL Server host, optionally with port. |
| `MSSQL_DB` | For SQL check | SQL Server database name. |
| `MSSQL_USER` | For SQL check | SQL Server username. |
| `MSSQL_PASS` | For SQL check | SQL Server password. |
| `MSSQL_DRIVER` | No | ODBC driver name. Default: `ODBC Driver 18 for SQL Server`. |
| `MSSQL_ENCRYPT` | No | SQL Server encryption setting. Default: `yes`. |
| `MSSQL_TRUST_SERVER_CERTIFICATE` | No | SQL Server certificate trust setting. Default: `yes`. |
| `PINECONE_API_KEY` | For Pinecone check | Pinecone API key. |
| `PINECONE_INDEX` | No | Optional Pinecone index to describe during status checks. |
| `OPENAI_API_KEY` | No | OpenAI API key for optional API status check. |

## Local Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Docker

```powershell
docker build -t smartherz-assistant-be:local .
docker run --rm -p 8000:8000 smartherz-assistant-be:local
```

Then open:

```text
http://localhost:8000/docs
http://localhost:8000/healthz
```

## GitHub Deploy Workflow

`.github/workflows/deploy.yml` builds the container, pushes it to Azure
Container Registry, and creates or updates Azure Container Apps.

Required repository secrets:

- `AZURE_CREDENTIALS`
- `OPENAI_API_KEY`
- `MSSQL_HOST`
- `MSSQL_DB`
- `MSSQL_USER`
- `MSSQL_PASS`
- `PINECONE_API_KEY`

Optional repository variables:

- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `AZURE_CONTAINER_APP_ENVIRONMENT`
- `AZURE_CONTAINER_APP_NAME`
- `AZURE_ACR_NAME`
- `AZURE_IMAGE_NAME`
