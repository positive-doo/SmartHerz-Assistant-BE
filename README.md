# SmartHerz Assistant Backend

FastAPI backend for the SmartHerz chatbot demo. It implements the API contract
already used by SmartHerz-FE and keeps the AgroTour integration as a local
OpenAI function tool. No MCP server or separate tool repository is required.
AgroTour search follows every API page until `has_more=false`, without a total
result limit or a client-selected page size. Requests for all matching businesses
use the complete result set; unrelated search results are not treated as matches.
Swagger is available at `/docs` when the service is running.

The `get_weather` assistant tool supplies MET Norway forecasts for the eight
Eastern Herzegovina municipalities, for today and the next six days. See
[weather configuration and limitations](docs/weather.md).

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check. |
| `GET` | `/api/status` | Checks backend, SQL Server, Pinecone, and optional OpenAI API connectivity. |
| `GET` | `/api/v1/integration/tourism` | Live public CSV POIs and route membership, plus WordPress destinations/events; integration API key required. |
| `GET` | `/api/v1/integration/kpis` | Aggregated recorded SQL sessions for an inclusive UTC date range; integration API key required. |
| `POST` | `/api/chat` | Answers a prompt using AgroTour, knowledge search, live CSV POIs/route membership, and WordPress events when needed. |
| `POST` | `/api/chat/stream` | Streams cumulative chat updates as server-sent events. |
| `POST` | `/initialize_session` | Initializes the session cookie expected by the existing frontend flow. |
| `POST` | `/tts` | Creates MP3 speech for supplied text or the latest assistant response. |
| `POST` | `/transcribe` | Transcribes the frontend's audio upload. |
| `POST` | `/feedback` | Stores feedback in `[features].[Feedback]`. |
| `POST` | `/save_pdf` | Renders the frontend's Markdown plan as a PDF. |

`/api/chat` sets an HTTP-only session cookie. SmartHerz-FE already sends
requests with `credentials: "include"`, so conversation history works without
changes to the frontend source.

`/api/chat/stream` accepts the same JSON body and emits `data:` events with
`content` and `done` fields. The original JSON endpoint remains available for
backward compatibility.

Both chat endpoints can call `get_tourism_catalogue` to read the same six public
CSV tabs as the integration export. The bot uses POI IDs, names, municipalities,
and membership of five named thematic routes. It fetches current data when the
model requests this tool; no new service, cache, ingestion job, or frontend
change is required. Detailed descriptions still come from knowledge search.
Route membership does not imply itinerary order, travel time, or distance.

Both chat endpoints also use `get_tourism_events` for current WordPress event
names, descriptions, dates, municipalities, and links. It shares the integration
export's event adapter and resolves municipality names through `mec_category`.
The tool includes the current UTC date and defaults to ongoing/future dated
events; `include_past=true` is used for explicit requests about past events.
The model receives today's UTC date before selecting tools. If the current
question names a past year, the backend includes historical records even when
the model incorrectly selects `include_past=false`; old conversation turns do
not activate this override.
The model applies the visitor's dates and municipality filters. Missing
times, location details, and organizers are not inferred. An unavailable source
is reported without substituting an old event list.

The tourism export includes destination `updated_at` from WordPress
`modified_gmt` in UTC, and an event `municipality` when a category is assigned.

When CSV data is used, chat JSON and the final SSE event include optional
`recommendations` with source IDs, names, categories and POI municipalities.
The frontend renders matching POI/route cards without requiring partner API
keys, missing images, or descriptions. Failed CSV retrieval sends an empty
card list; conversations which do not use CSV retain the existing card flow.

After each completed successful answer, the backend upserts anonymous session
metadata into the existing `chat.Threads` table. Turns in the same in-memory
session share one thread ID. Only app ID, a random thread ID, the UTC creation
time and latest message time are stored; no message text or visitor details.
This supplies new session counts to the existing KPI endpoint after rollout.
Recommendation counts and session duration remain unavailable (`null`); the
last activity time is not treated as a measured session close time.

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
| `BM25_STATS_URL` | Alternative for hybrid search | Private read-only URL for the same JSON; used only when no path is set. |
| `OPENAI_API_KEY` | For chat | OpenAI API key. |
| `OPENAI_MODEL` | No | Model used by the assistant. Default: `gpt-5-mini`. |
| `TTS_MODEL` | No | Speech model. Default: `gpt-4o-mini-tts`. |
| `TTS_VOICE` | No | Speech voice. Default: `cedar`. |
| `TRANSCRIPTION_MODEL` | No | Transcription model. Default: `gpt-transcribe`. |
| `TRANSCRIPTION_LANGUAGE` | No | Transcription language hint. Default: `sr`. |
| `AGROTOUR_API_KEY` | For AgroTour search | Partner key sent in the `X-API-Key` header. |
| `AGROTOUR_SEARCH_URL` | No | Search endpoint. Default: `https://agrotour.eu/api/v1/search`. |
| `CORS_ORIGINS` | No | Comma-separated frontend origins. Defaults to local port `3000`. |
| `APP_ID` | No | SmartHerz application ID for prompts, feedback and KPI aggregates. Default: `66`. |
| `CLIENT_ID` | No | SmartHerz client ID. Default: `18`. |
| `TRAVEL_PLAN_TEMPLATE_PATH` | No | Optional path to the DOCX template used for travel-plan exports. |
| `INTEGRATION_KEY_SECRET` | For partner key delivery | Server-only secret shared with AdminApp's `SMARTHERZ_INTEGRATION_KEY_SECRET`. Access is verified in `admin.AdminApiKeys`. |
| `INTEGRATION_EVENTS_URL` | No | WordPress MEC collection URL; defaults to the public SmartHerz portal. |

For Azure Container Apps, store the private BM25 URL as a secret (for example,
`bm25-stats-url`) and map `BM25_STATS_URL` to that secret during deployment.
Never commit the URL or its SAS token.

## Local Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

On Windows, PDF export uses `docx2pdf` and requires Microsoft Word to be
installed. On Linux and in the provided Docker image, PDF export uses
LibreOffice in headless mode.

Travel-plan day headings should use `# Dan N — kratak opis dana`. The PDF
renderer turns that heading into a colored two-column day block with the day
number on the left and the label plus description on the right. The document
uses Merriweather for headings and Source Sans 3 for body text. These fonts are
bundled in `assets/fonts` and installed automatically by the Docker image; for
local Windows conversion they should also be installed in Windows so Word does
not substitute another font.

Example request:

```powershell
Invoke-RestMethod http://localhost:8000/api/chat `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"Preporuči mi restoran u Trebinju"}'
```

## Docker

SMART ERA setup, examples and SQL coverage limitations are described in
[the integration documentation](docs/smart-era-integration.md).

To install test dependencies and run the backend suite:

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

```powershell
docker build -t smartherz-assistant-be:local .
docker run --rm -p 8000:8000 smartherz-assistant-be:local
```

Then open:

```text
http://localhost:8000/docs
http://localhost:8000/healthz
```

## Deployment

This repository currently has no GitHub Actions deployment workflow. Pushing
to `main` does not deploy the backend through a workflow in this repository.
Updating the Azure Container App is a separate deployment step; keep service
credentials and the server-only `INTEGRATION_KEY_SECRET` in Azure secrets.
Partner API keys and revocation are managed through the existing AdminApp API keys page
for client `18`, app `66`; see the integration documentation for one-time link/PIN delivery.
