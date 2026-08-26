# SmartHerz Assistant Backend

FastAPI backend for the SmartHerz chatbot demo. It implements the API contract
already used by SmartHerz-FE and keeps the AgroTour integration as a local
OpenAI function tool. No MCP server or separate tool repository is required.
Swagger is available at `/docs` when the service is running.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check. |
| `GET` | `/api/status` | Checks backend, SQL Server, Pinecone, and optional OpenAI API connectivity. |
| `POST` | `/api/chat` | Answers a prompt and uses local AgroTour and knowledge-search tools when needed. |
| `POST` | `/initialize_session` | Initializes the session cookie expected by the existing frontend flow. |
| `POST` | `/tts` | Creates MP3 speech for supplied text or the latest assistant response. |
| `POST` | `/transcribe` | Transcribes the frontend's audio upload. |
| `POST` | `/feedback` | Stores feedback in `[features].[Feedback]`. |
| `POST` | `/save_pdf` | Renders the frontend's Markdown plan as a PDF. |

`/api/chat` sets an HTTP-only session cookie. SmartHerz-FE already sends
requests with `credentials: "include"`, so conversation history works without
changes to the frontend source.

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
| `PINECONE_INDEX` | No | Optional index to describe during status checks. Hybrid search uses `neo-positive`. |
| `BM25_STATS_PATH` | For hybrid search | Path to the non-committed `smartherz` BM25 statistics JSON. |
| `BM25_STATS_URL` | Alternative for hybrid search | Private deployment URL for the same JSON; used only when no path is set. |
| `OPENAI_API_KEY` | For chat | OpenAI API key. |
| `OPENAI_MODEL` | No | Model used by the assistant. Default: `gpt-5-mini`. |
| `TTS_MODEL` | No | Speech model. Default: `gpt-4o-mini-tts`. |
| `TTS_VOICE` | No | Speech voice. Default: `cedar`. |
| `TRANSCRIPTION_MODEL` | No | Transcription model. Default: `gpt-transcribe`. |
| `TRANSCRIPTION_LANGUAGE` | No | Transcription language hint. Default: `sr`. |
| `AGROTOUR_API_KEY` | For AgroTour search | Partner key sent in the `X-API-Key` header. |
| `AGROTOUR_SEARCH_URL` | No | Search endpoint. Default: `https://dev.agrotour.eu/api/v1/search`. |
| `CORS_ORIGINS` | No | Comma-separated frontend origins. Defaults to local port `3000`. |
| `APP_ID` | No | SmartHerz application ID for prompts and feedback. Default: `66`. |
| `CLIENT_ID` | No | SmartHerz client ID. Default: `18`. |
| `PDF_FONT_PATH` | No | Optional Unicode TrueType font path for PDF rendering. |

## Local Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Example request:

```powershell
Invoke-RestMethod http://localhost:8000/api/chat `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"Preporuči mi restoran u Trebinju"}'
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
