# SMART-HERZ / SMART ERA integracija

Lokalno implementirana dva GET endpointa. KPI koristi stvarni SQL. Tourism trenutno koristi demo POI-je, destinacije i rute, uz stvarne događaje iz WordPress-a. Konačno povezivanje POI izvora čeka zvanične CSV URL-ove i stabilne ID-jeve.

Osnovni podaci osam destinacija i pet ruta (`id`, naziv, `slug`, URL) već su dostupni kroz javni WordPress `/wp-json/wp/v2/pages`. Za te podatke nije potreban novi WordPress endpoint niti administrativni pristup. Opisi na Divi stranicama nisu strukturisani kao izdvojeni opisi destinacija/ruta; neke stranice imaju prazno telo. Trenutna implementacija i dalje koristi dogovoreni demo katalog. Povezivanje osnovnih stvarnih destinacija/ruta može koristiti postojeći API, dok CSV ostaje potreban za POI podatke i liste lokacija po rutama.

## Pokretanje lokalno

U direktorijumu backend repozitorijuma, uz instalirane `requirements.txt` i ODBC Driver 18:

```powershell
$env:INTEGRATION_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:INTEGRATION_TOURISM_DEMO = "true"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Postojeće promenljive `MSSQL_HOST`, `MSSQL_DB`, `MSSQL_USER` i `MSSQL_PASS` moraju biti dostupne procesu. Koristi se postojeća `_sql_connection()` i `APP_ID` (podrazumevano 66). KPI ne koristi demo statistiku i ne upisuje podatke u SQL.

Lokalni base URL je `http://127.0.0.1:8000`. OpenAPI je na `/docs`, gde dugme **Authorize** prima integration ključ. Ključ generisan gornjom komandom važi dok ga držite u konfiguraciji; ne generiše se ponovo po zahtevu.

Azure base URL je `https://smartherz-be.livelyflower-1c718338.eastus.azurecontainerapps.io`. Izmene nisu deploy-ovane; novi endpointi na toj adresi još nisu dostupni. Pri kasnijem deploy-u zaseban integration ključ treba čuvati kao Azure secret vezan za `INTEGRATION_API_KEY`. AgroTour ključ nije ključ za ove endpoint-e.

## Autentifikacija i konfiguracija

Oba endpointa traže zaglavlje:

```http
X-API-Key: <integration-api-key>
```

| Promenljiva | Značenje |
| --- | --- |
| `INTEGRATION_API_KEY` | Jedan statičan ključ za oba endpointa; obavezan. |
| `INTEGRATION_TOURISM_DEMO` | Postaviti `true` za lokalni demo katalog. Podrazumevano je isključen. |
| `INTEGRATION_EVENTS_URL` | Opcionalno; podrazumevano `https://smart-herz.modoos.rs/wp-json/wp/v2/mec-events`. |

Nedostajući/pogrešan ključ vraća 401. Ako integration ključ nije konfigurisan, vraća se 503. SMART ERA potvrda načina autentifikacije potrebna je pre povezivanja.

## Turistički podaci

`GET /api/v1/integration/tourism`

Odgovor sadrži `generated_at` (UTC), `places`, `routes` i `events`. `places` trenutno sadrži osam demo destinacija i dva demo POI-ja, a `routes` jednu demo rutu. Demo ID-jevi imaju prefiks `demo_`, a nazivi oznaku `DEMO`. Događaji imaju stvarne WordPress ID-jeve sa prefiksom `event_`. Zaglavlje odgovora `X-SmartHerz-Data-Mode: demo-catalogue-live-events` označava ovaj privremeni način rada.

Osnovna polja zapisa su `id`, `name`, opciono `description` i `url`. `places` ima i `category` (`destination` za opštine) i opcioni `slug`. Događaji imaju dostupne `start_date` i `end_date` u formatu `YYYY-MM-DD`; to su lokalni datumi portala `Europe/Sarajevo`, bez izmišljanja vremena početka. HTML naslova/opisa pretvara se u običan tekst. Nepopunjena tourism polja se izostavljaju.

Skraćen primer:

```json
{
  "generated_at": "2026-10-13T08:00:00Z",
  "places": [
    {"id": "demo_destination_trebinje", "name": "DEMO – Trebinje", "category": "destination", "slug": "trebinje", "description": "Demo zapis za lokalno testiranje integracije."},
    {"id": "demo_poi_001", "name": "DEMO – Turistička lokacija 1", "category": "cultural_heritage", "description": "Izmišljeni POI za proveru JSON odgovora."}
  ],
  "routes": [
    {"id": "demo_route_001", "name": "DEMO – Tematska ruta", "description": "Izmišljena ruta za lokalno testiranje."}
  ],
  "events": [
    {"id": "event_1153", "name": "Dabarska kotlićijada", "url": "https://smart-herz.modoos.rs/events/dabarska-kotlicijada/", "start_date": "2027-06-06", "end_date": "2027-06-06"}
  ]
}
```

Svaki poziv preuzima sve WordPress stranice događaja i proverava broj zapisa prema `X-WP-Total`, kao i jedinstvenost ID-jeva. Neuspešno preuzimanje, nepotpun spisak ili neispravan izvor vraća 503, bez delimičnog uspešnog izvoza. Prazna lista događaja je dozvoljena kada API uspešno potvrdi da ih nema. Bez eksplicitnog demo režima, tourism vraća 503 dok stvarni izvori nisu povezani.

SMART ERA planira polling jednom dnevno i potpunu sinhronizaciju. Demo katalog služi za lokalni razvoj i nije spreman za produkcionu sinhronizaciju. Osnovne destinacije/rute moguće je povezati sa postojećim WordPress API-jem; nakon dobijanja CSV izvora treba povezati stvarne POI podatke, potvrditi stabilne ID-jeve i proveriti kompletan izvoz.

## KPI statistika

`GET /api/v1/integration/kpis?date_from=2026-07-01&date_to=2026-07-31`

Oba datuma su obavezna, striktno `YYYY-MM-DD`, uključiva i sa UTC značenjem. Primer koristi interval `[2026-07-01T00:00:00Z, 2026-08-01T00:00:00Z)`. Neispravni datumi, obrnut raspon i završni datum `9999-12-31` (nema predstavljivog sledećeg dana) vraćaju 422.

```json
{
  "date_from": "2026-07-01",
  "date_to": "2026-07-31",
  "ai_conversations": 25,
  "recommendations_generated": null,
  "average_session_duration_seconds": null
}
```

- `ai_conversations`: broj različitih `thread_id` u `chat.Threads`, za `APP_ID`, čiji `created_at` pripada periodu. Razgovor znači sesiju. Starija `dbo.Threads` se ne sabira.
- `recommendations_generated`: `null`, jer broj preporuka nije zasebno evidentiran.
- `average_session_duration_seconds`: prosek validnih `closed_at - created_at` za sesije započete u periodu; `null` kada nema zabeleženih završetaka. Negativna trajanja se ne uključuju. Ne procenjuje se trajanje iz poruka.

SQL provera 13.10.2026. pokazuje ukupno 42 sesije, poslednju od 21.08.2026. Aktuelni backend nove razgovore čuva u memoriji i ne upisuje nove SQL sesije. Zato broj opisuje samo postojeće evidentirane SQL sesije; nula za oktobar ne dokazuje odsustvo upotrebe aplikacije. Sva postojeća trajanja su nedostupna. Nova telemetrija nije deo implementacije.

Odgovor sadrži samo agregate, bez identifikatora korisnika/sesija i sadržaja razgovora. SQL nedostupnost vraća 503; ne zamenjuje se nulom ili demo podacima.

## Primeri poziva i provera

Dok server radi, iz PowerShell-a sa istim `INTEGRATION_API_KEY`:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/integration/kpis?date_from=2026-07-01&date_to=2026-07-31' -Headers @{'X-API-Key' = $env:INTEGRATION_API_KEY}
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/integration/tourism' -Headers @{'X-API-Key' = $env:INTEGRATION_API_KEY}
```

Testovi koriste lokalne primere za WordPress i SQL odgovore, bez testnih upisa u stvarnu bazu. Razvojne zavisnosti uključuju `httpx` za TestClient.

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Statusi: 200 uspešan odgovor; 401 autentifikacija; 422 datumi; 503 konfiguracija ili izvor nisu dostupni.
