# SMART-HERZ / SMART ERA integracija

Implementirana su dva GET endpointa. KPI koristi stvarni SQL. Početni tourism izvoz obuhvata 14 stvarnih POI-ja iz postojećeg frontend kataloga, uz osam destinacija, pet ruta i dostupne događaje iz javnog WordPress API-ja. To je ograničeni početni skup, a ne kompletan katalog svih turističkih lokacija regiona.

Osnovni podaci destinacija/ruta preuzimaju se kroz javni WordPress `/wp-json/wp/v2/pages`, prema poznatim ID-jevima tih stranica. Za njih nije potreban novi WordPress endpoint niti administrativni pristup. Destinacije izlažu `id`, `name`, `slug`, `url` i `category=destination`; rute `id`, `name` i `url`. Divi sadržaj nije izdvojen opis destinacije/rute, pa se ne izvozi kao opis. POI zapisi u `data/integration_pois.json` preuzeti su iz `SmartHerz-Assistant-FE/src/data/rightSuggestions.ts`: sačuvani su postojeći `poi-*` ID-jevi, nazivi, opisi na lokalnom jeziku i izvorni URL-ovi. Katalog se menja kroz verzionisanje tog fajla. Geografske koordinate i liste lokacija po rutama nisu deo ovog početnog skupa.

## Pokretanje lokalno

U direktorijumu backend repozitorijuma, uz instalirane `requirements.txt` i ODBC Driver 18:

```powershell
$env:INTEGRATION_KEY_SECRET = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Postojeće promenljive `MSSQL_HOST`, `MSSQL_DB`, `MSSQL_USER` i `MSSQL_PASS` moraju biti dostupne procesu. Koristi se postojeća `_sql_connection()` i `APP_ID` (podrazumevano 66). KPI ne koristi demo statistiku i ne upisuje podatke u SQL.

Lokalni base URL je `http://127.0.0.1:8000`. OpenAPI je na `/docs`, gde dugme **Authorize** prima partnerski API ključ preuzet jednokratnim linkom. Gornja promenljiva je interna serverska tajna; ne šalje se partnerima niti služi za autentifikaciju API poziva.

Produkcioni base URL je `https://smartherz-be.livelyflower-1c718338.eastus.azurecontainerapps.io`. Interna tajna čuva se kao Azure secret vezan za `INTEGRATION_KEY_SECRET`. Svaki partner ima zaseban API ključ u MSSQL evidenciji. AgroTour ključ nije ključ za ove endpoint-e.

## Autentifikacija i konfiguracija

Oba endpointa traže zaglavlje:

```http
X-API-Key: <integration-api-key>
```

| Promenljiva | Značenje |
| --- | --- |
| `INTEGRATION_KEY_SECRET` | Interna tajna za generisanje/predaju partnerskih ključeva; najmanje 32 nasumična znaka. Čuvati trajno i jednako na svim replikama. |
| `INTEGRATION_EVENTS_URL` | Opcionalno; podrazumevano `https://smart-herz.modoos.rs/wp-json/wp/v2/mec-events`. |

Nedostajući, nepoznat ili ukinut ključ vraća 401. Svaki API poziv proverava hash ključa i status partnera u SQL-u, bez keširanja. Nedostupna baza vraća 503. Prethodni zajednički `INTEGRATION_API_KEY` više se ne prihvata. SMART ERA potvrda načina autentifikacije potrebna je pre povezivanja.

## Jednokratno preuzimanje ključa

Backend ima komandu za kreiranje linka i jednostavnu stranicu za preuzimanje uz PIN. Mejl sa linkom šalje operator ručno. Silo je bio samo referenca; ovo rešenje ga ne koristi.

Evidencija se čuva u postojećem MSSQL-u: **`devplay.SmartHerzPartners`** sadrži partnera, nasumični identifikator ključa, hash API ključa, vreme izdavanja i vreme ukidanja; postojeća **`devplay.SmartHerzKeyLinks`** čuva jednokratne linkove. Pokrenite `sql/001_smartherz_key_links.sql`, pa `sql/002_smartherz_partners.sql`. Obe skripte su bezbedne za ponovno pokretanje. Šema `devplay` mora već da postoji. Backend/operator nalogu potrebna su prava `SELECT`, `INSERT`, `UPDATE` i `DELETE` na ovim tabelama. Zahtevi aplikacije ne kreiraju tabele.

Koriste se postojeći `MSSQL_*` parametri i nova interna tajna `INTEGRATION_KEY_SECRET`. Nema dodatne baze, servisa niti administrativnog web endpointa. Lokalni server pokrenite sa tim parametrima:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

U drugom terminalu, sa **istom internom tajnom i istim MSSQL parametrima**, kreirajte partnera i prvi link:

```powershell
python integration_key_links.py create --partner smart-era --base-url http://127.0.0.1:8000 --expires-hours 1
```

Komanda vraća JSON sa `partner`, `url`, četvorocifrenim `pin` i `expires_at`, bez API ključa. Partner je jedinstvena oznaka od 1–100 malih slova, cifara, crtica ili donjih crta. Rok može biti 1, 24 ili 168 sati (podrazumevano 7 dana). Link šaljite mejlom; PIN po mogućnosti drugim kanalom.

Za novi link za isti aktivni ključ i za ukidanje pristupa:

```powershell
python integration_key_links.py link --partner smart-era --base-url https://smartherz-be.livelyflower-1c718338.eastus.azurecontainerapps.io
python integration_key_links.py revoke --partner smart-era
```

`link` zadržava postojeći ključ. `revoke` ukida pristup samo tom partneru, uključujući sve njegove neiskorišćene linkove, bez restarta/deploy-a. Ponovljeni `revoke` je bezbedan. Za ponovno odobravanje ukinutom partneru koristite `create`: dobija novi ključ i link; stari ključ i stari linkovi ostaju nevažeći. `create` odbija partnera koji već ima aktivan ključ, da ne bi slučajno prekinuo njegovu integraciju.

- `GET /integration/key#<token>` prikazuje stranicu. Samo otvaranje/pretpregled linka ga ne troši; token iz fragmenta ne odlazi u URL serverskog zahteva.
- Dugme šalje token i PIN na `POST /api/v1/integration/key-links/redeem`. Uspešan odgovor vraća ključ jednom; ponovni pokušaj ili istekao link vraća 410, pogrešan PIN 403. Posle pet pogrešnih PIN-ova link se poništava.
- API ključ ostaje važeći za oba integraciona endpointa i posle iskorišćavanja linka, sve dok pristup tom partneru ne bude ukinut. Ukidanje ne utiče na druge partnere. Zahtev koji je već prošao proveru pre ukidanja može završiti svoj odgovor.
- Evidencija čuva hash tokena, proveru PIN-a i ključa, rok i broj pokušaja. Ne čuva sam API ključ, token ili PIN. Provera, promena broja pokušaja i brisanje rade u SQL transakciji uz `UPDLOCK, HOLDLOCK`. Ključ se vraća tek posle uspešnog commit-a. Zato samo jedan istovremeni zahtev može preuzeti ključ, uključujući zahteve različitih procesa/replika.
- Rok se određuje i proverava prema SQL UTC vremenu (`SYSUTCDATETIME()`), a istekli zapisi se čiste pri kreiranju novog linka. Poseban zakazani posao nije potreban.
- Ako interna tajna nije podešena ili SQL/tabela nisu dostupni pri kreiranju/preuzimanju, vraća se 503. Pogrešno podešena interna tajna ne troši ispravan link niti vraća drugi ključ. Stranica i odgovori sa ključem imaju `Cache-Control: no-store`.

SQL evidencija opstaje posle restarta i zamene kontejnera. Sve Azure replike i administratorska komanda moraju koristiti istu bazu i istu internu tajnu `INTEGRATION_KEY_SECRET`. Partnerski ključ izvodi se preko HMAC-SHA256 iz te tajne i nasumičnog identifikatora ključa, uz posebnu oznaku namene. Tako baza ne čuva čitljive API ključeve, a novi link može predati isti aktivni ključ. Internu tajnu sačuvati u Azure secrets; njena promena zahteva ponovno izdavanje partnerskih ključeva za dalju predaju. Stari zajednički ključ i njegovi linkovi ne važe u ovom sistemu. Mejlovi se šalju ručno.

## Turistički podaci

`GET /api/v1/integration/tourism`

Odgovor sadrži `generated_at` (UTC), `places`, `routes` i `events`. `places` sadrži osam destinacija i 14 POI-ja, a `routes` pet ruta. WordPress ID-jevi imaju prefikse `destination_`, `route_` i `event_`; POI-jevi zadržavaju postojeće frontend ID-jeve sa prefiksom `poi-`. Zaglavlje `X-SmartHerz-Data-Mode: curated-pois-live-wordpress` označava lokalno uređeni POI katalog i žive WordPress izvore. Nema demo zapisa.

Osnovna polja zapisa su `id`, `name`, opciono `description` i `url`. `places` ima i `category` (`destination` za opštine) i opcioni `slug`. Događaji imaju dostupne `start_date` i `end_date` u formatu `YYYY-MM-DD`; to su lokalni datumi portala `Europe/Sarajevo`, bez izmišljanja vremena početka. HTML naslova/opisa pretvara se u običan tekst. Nepopunjena tourism polja se izostavljaju.

Skraćen primer:

```json
{
  "generated_at": "2026-10-13T08:00:00Z",
  "places": [
    {"id": "destination_347", "name": "Trebinje", "category": "destination", "slug": "trebinje", "url": "https://smart-herz.modoos.rs/destinacije/trebinje/"},
    {"id": "poi-trebisnjica", "name": "Trebišnjica", "category": "poi", "url": "https://smart-herz.modoos.rs/destinacije/trebinje"}
  ],
  "routes": [
    {"id": "route_1590", "name": "Staza ukusa – Istočna Hercegovina", "url": "https://smart-herz.modoos.rs/tematske-rute/staza-ukusa-istocna-hercegovina/"}
  ],
  "events": [
    {"id": "event_1153", "name": "Dabarska kotlićijada", "url": "https://smart-herz.modoos.rs/events/dabarska-kotlicijada/", "start_date": "2027-06-06", "end_date": "2027-06-06"}
  ]
}
```

Svaki poziv preuzima osnovne podatke 13 poznatih stranica destinacija/ruta i sve WordPress stranice događaja. Proverava broj zapisa prema `X-WP-Total`, jedinstvenost ID-jeva i prisustvo svih očekivanih destinacija/ruta. Neuspešno preuzimanje, nepotpun spisak ili neispravan izvor vraća 503, bez delimičnog uspešnog izvoza. Prazna lista događaja je dozvoljena kada API uspešno potvrdi da ih nema. Nedostupan ili neispravan lokalni POI fajl takođe vraća 503; nema delimičnog uspešnog izvoza.

SMART ERA planira polling jednom dnevno i potpunu sinhronizaciju dogovorenog skupa. Početni skup je namerno ograničen na postojeći uređeni katalog; ne predstavlja sve POI-je u svih osam opština. Potpuniji CSV izvor može se povezati naknadno. Geometrija ruta, redosled stanica i opisi koji nisu strukturisani ne izmišljaju se.

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

SQL provera 16.10.2026. pokazuje ukupno 42 sesije, poslednju od 21.08.2026. Aktuelni backend nove razgovore čuva u memoriji i ne upisuje nove SQL sesije. Zato broj opisuje samo postojeće evidentirane SQL sesije; nula za oktobar ne dokazuje odsustvo upotrebe aplikacije. Sva postojeća trajanja su nedostupna. Nova telemetrija nije deo implementacije.

Odgovor sadrži samo agregate, bez identifikatora korisnika/sesija i sadržaja razgovora. SQL nedostupnost vraća 503; ne zamenjuje se nulom ili demo podacima.

## Primeri poziva i provera

Dok server radi, iz PowerShell-a sa partnerskim API ključem preuzetim preko linka:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/integration/kpis?date_from=2026-07-01&date_to=2026-07-31' -Headers @{'X-API-Key' = $env:PARTNER_API_KEY}
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/integration/tourism' -Headers @{'X-API-Key' = $env:PARTNER_API_KEY}
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

Ovi dodatni testovi kreiraju privremene partnere/linkove samo u dve `devplay.SmartHerz*` tabele i čiste svoje zapise nakon provere. Pokrivaju odvojene ključeve, pojedinačno ukidanje, ponovno izdavanje, PIN, istek, pogrešnu internu tajnu, ponovni pristup, novi proces i istovremene zahteve. Kreiranje linka, kao i u redovnom radu, uklanja istekle linkove.

Statusi: 200 uspešan odgovor; 401 autentifikacija; 422 datumi; 503 konfiguracija ili izvor nisu dostupni.

## Produkciona konfiguracija

AgroTour koristi `AGROTOUR_SEARCH_URL=https://agrotour.eu/api/v1/search` i postojeći zaseban `AGROTOUR_API_KEY`. Taj ključ nije integration ključ za SMART ERA.

`CORS_ORIGINS` i Azure ingress CORS dozvoljavaju Storage frontend origin i stvarni Front Door origin `https://positivefrontend-erauhbdtd7c7dzed.z02.azurefd.net`. SQL pooling ostaje uključen u `database.py` i Docker unixODBC konfiguraciji; ova integracija ga ne menja.
