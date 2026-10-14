# SMART-HERZ / SMART ERA integracija

Lokalno implementirana dva GET endpointa. KPI koristi stvarni SQL. Tourism koristi stvarne osnovne podatke osam destinacija, pet ruta i događaje iz WordPress-a. Demo ostaju samo dva POI-ja, dok ne stignu zvanični CSV URL-ovi i stabilni ID-jevi.

Osnovni podaci destinacija/ruta preuzimaju se kroz javni WordPress `/wp-json/wp/v2/pages`, prema poznatim ID-jevima tih stranica. Za njih nije potreban novi WordPress endpoint niti administrativni pristup. Destinacije izlažu `id`, `name`, `slug`, `url` i `category=destination`; rute `id`, `name` i `url`. Divi sadržaj nije izdvojen opis destinacije/rute, pa se ne izvozi kao opis. CSV ostaje potreban za stvarne POI podatke i liste lokacija po rutama.

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
| `INTEGRATION_TOURISM_DEMO` | Postaviti `true` za lokalne demo POI-je dok CSV nije povezan. Podrazumevano je isključen. |
| `INTEGRATION_EVENTS_URL` | Opcionalno; podrazumevano `https://smart-herz.modoos.rs/wp-json/wp/v2/mec-events`. |

Nedostajući/pogrešan ključ vraća 401. Ako integration ključ nije konfigurisan, vraća se 503. SMART ERA potvrda načina autentifikacije potrebna je pre povezivanja.

## Jednokratno preuzimanje ključa

Backend ima komandu za kreiranje linka i jednostavnu stranicu za preuzimanje uz PIN. Mejl sa linkom šalje operator ručno. Silo je bio samo referenca; ovo rešenje ga ne koristi.

Evidencija se čuva u postojećem MSSQL-u, u jednoj tabeli **`devplay.SmartHerzKeyLinks`**. Jednom pokrenite `sql/001_smartherz_key_links.sql` u bazi koju backend koristi; skripta može bezbedno da se pokrene ponovo. Šema `devplay` mora već da postoji. Backend nalogu potrebna su prava `SELECT`, `INSERT`, `UPDATE` i `DELETE` na toj tabeli. Zahtevi aplikacije ne kreiraju tabele.

Koriste se postojeći `MSSQL_*` parametri i `INTEGRATION_API_KEY`. Nema SQLite datoteke, dodatne baze ili servisa. Lokalni server pokrenite sa tim parametrima:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

U drugom terminalu, sa **istim ključem i istim MSSQL parametrima**, kreirajte link:

```powershell
python integration_key_links.py --base-url http://127.0.0.1:8000 --expires-hours 1
```

Komanda vraća JSON sa `url`, četvorocifrenim `pin` i `expires_at`. Rok može biti 1, 24 ili 168 sati (podrazumevano 7 dana). Link šaljite mejlom; PIN po mogućnosti drugim kanalom. Komanda predaje postojeći ključ; ne menja ga i ne štampa njegovu vrednost. Novi ključ se generiše samo kada operator namerno želi da ga postavi/zameni.

- `GET /integration/key#<token>` prikazuje stranicu. Samo otvaranje/pretpregled linka ga ne troši; token iz fragmenta ne odlazi u URL serverskog zahteva.
- Dugme šalje token i PIN na `POST /api/v1/integration/key-links/redeem`. Uspešan odgovor vraća ključ jednom; ponovni pokušaj ili istekao link vraća 410, pogrešan PIN 403. Posle pet pogrešnih PIN-ova link se poništava.
- API ključ ostaje važeći za oba integraciona endpointa i posle iskorišćavanja linka. Promena API ključa poništava ranije linkove; oni ne otkrivaju novi ključ.
- Evidencija čuva hash tokena, proveru PIN-a i ključa, rok i broj pokušaja. Ne čuva sam API ključ, token ili PIN. Provera, promena broja pokušaja i brisanje rade u SQL transakciji uz `UPDLOCK, HOLDLOCK`. Ključ se vraća tek posle uspešnog commit-a. Zato samo jedan istovremeni zahtev može preuzeti ključ, uključujući zahteve različitih procesa/replika.
- Rok se određuje i proverava prema SQL UTC vremenu (`SYSUTCDATETIME()`), a istekli zapisi se čiste pri kreiranju novog linka. Poseban zakazani posao nije potreban.
- Ako ključ nije podešen ili SQL/tabela nisu dostupni pri kreiranju/preuzimanju, vraća se 503. Stranica i odgovori sa ključem imaju `Cache-Control: no-store`.

SQL evidencija opstaje posle restarta i zamene kontejnera. Sve Azure replike i administratorska komanda moraju koristiti istu bazu i isti `INTEGRATION_API_KEY`. API ključ se i dalje čuva kao Azure secret. Promenljiva `INTEGRATION_KEY_LINKS_DB` više se ne koristi. Ove izmene ne šalju mejlove i ne izvršavaju deploy.

## Turistički podaci

`GET /api/v1/integration/tourism`

Odgovor sadrži `generated_at` (UTC), `places`, `routes` i `events`. `places` sadrži osam stvarnih destinacija i dva demo POI-ja, a `routes` pet stvarnih ruta. WordPress ID-jevi dobijaju prefikse `destination_`, `route_` i `event_`; samo demo POI-jevi imaju prefiks `demo_` i oznaku `DEMO` u nazivu. Zaglavlje odgovora `X-SmartHerz-Data-Mode: demo-pois-live-wordpress` označava ovaj privremeni način rada.

Osnovna polja zapisa su `id`, `name`, opciono `description` i `url`. `places` ima i `category` (`destination` za opštine) i opcioni `slug`. Događaji imaju dostupne `start_date` i `end_date` u formatu `YYYY-MM-DD`; to su lokalni datumi portala `Europe/Sarajevo`, bez izmišljanja vremena početka. HTML naslova/opisa pretvara se u običan tekst. Nepopunjena tourism polja se izostavljaju.

Skraćen primer:

```json
{
  "generated_at": "2026-10-13T08:00:00Z",
  "places": [
    {"id": "destination_347", "name": "Trebinje", "category": "destination", "slug": "trebinje", "url": "https://smart-herz.modoos.rs/destinacije/trebinje/"},
    {"id": "demo_poi_001", "name": "DEMO – Turistička lokacija 1", "category": "cultural_heritage", "description": "Izmišljeni POI za proveru JSON odgovora."}
  ],
  "routes": [
    {"id": "route_1590", "name": "Staza ukusa – Istočna Hercegovina", "url": "https://smart-herz.modoos.rs/tematske-rute/staza-ukusa-istocna-hercegovina/"}
  ],
  "events": [
    {"id": "event_1153", "name": "Dabarska kotlićijada", "url": "https://smart-herz.modoos.rs/events/dabarska-kotlicijada/", "start_date": "2027-06-06", "end_date": "2027-06-06"}
  ]
}
```

Svaki poziv preuzima osnovne podatke 13 poznatih stranica destinacija/ruta i sve WordPress stranice događaja. Proverava broj zapisa prema `X-WP-Total`, jedinstvenost ID-jeva i prisustvo svih očekivanih destinacija/ruta. Neuspešno preuzimanje, nepotpun spisak ili neispravan izvor vraća 503, bez delimičnog uspešnog izvoza. Prazna lista događaja je dozvoljena kada API uspešno potvrdi da ih nema. Bez eksplicitnog demo režima, tourism vraća 503 dok stvarni POI izvor nije povezan.

SMART ERA planira polling jednom dnevno i potpunu sinhronizaciju. Odgovor koji sadrži demo POI-je služi za lokalni razvoj i nije spreman za produkcionu sinhronizaciju. Nakon dobijanja CSV izvora treba povezati stvarne POI podatke, potvrditi stabilne ID-jeve i proveriti kompletan izvoz. Geometrija ruta, redosled stanica i opisi koji nisu strukturisani ne izmišljaju se.

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

Podrazumevani testovi koriste lokalne primere za WordPress i SQL odgovore, bez testnih upisa u stvarnu bazu. Razvojne zavisnosti uključuju `httpx` za TestClient.

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Za testiranje jednokratnih linkova nad stvarnim MSSQL-om, uz postojeću tabelu i `MSSQL_*` parametre:

```powershell
$env:SMARTHERZ_TEST_MSSQL = "1"
python -m unittest discover -s tests -v
```

Ovi dodatni testovi kreiraju privremene linkove samo u `devplay.SmartHerzKeyLinks` i čiste svoje zapise nakon provere. Pokrivaju PIN, istek, promenu ključa, ponovni pristup, novi proces i istovremene zahteve. Kreiranje linka, kao i u redovnom radu, uklanja istekle zapise iz ove tabele.

Statusi: 200 uspešan odgovor; 401 autentifikacija; 422 datumi; 503 konfiguracija ili izvor nisu dostupni.
