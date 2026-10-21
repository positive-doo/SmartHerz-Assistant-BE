# SMART-HERZ / SMART ERA integracija

Implementirana su dva GET endpointa. KPI koristi stvarni SQL. Tourism preuzima osnovne POI podatke i pripadnost rutama direktno iz šest javnih Google CSV tabova pri svakom pozivu. Provera 21.10.2026. obuhvatila je 281 POI, osam destinacija, pet ruta i 36 događaja; broj zapisa prati promene izvora.

Osnovni podaci destinacija/ruta preuzimaju se kroz javni WordPress `/wp-json/wp/v2/pages`, prema poznatim ID-jevima tih stranica. Za njih nije potreban novi WordPress endpoint niti administrativni pristup. Destinacije izlažu `id`, `name`, `slug`, `url`, `category=destination` i `updated_at` iz WordPress `modified_gmt`, sa UTC vremenskom zonom; rute `id`, `name`, `url` i `poi_ids`. Divi sadržaj nije izdvojen opis destinacije/rute, pa se ne izvozi kao opis. POI podaci dolaze iz kartice `svi POI`: ID, naziv i opština. Liste `poi_ids` dolaze iz pet tabova ruta, sa po jednim istim ID-jem unutar rute. Opisi, koordinate i slike iz drugih tabova nisu deo ovog izvoza.

`integration_tourism.py` čita CSV standardnom Python bibliotekom; nema novog servisa, raspoređivača, tabele ili runtime zavisnosti. Pri svakom zahtevu preuzima svih šest tabova, proverava zaglavlja, jedinstvene ID-jeve, nazive, opštine i reference ruta. Ne izvršava formule. Nema lokalnog keša niti rezervnog starog kataloga. Izvorni XLSX korišćen je za nezavisnu proveru i testni primer u `tests/fixtures/tourism_20261020.json`; backend ga ne čita.

Javni CSV izvori pronađeni su u otvorenoj izvornoj Google tabeli i provereni 20.10.2026. Svih šest URL-ova vraća HTTP 200 bez kolačića, API ključa ili prijavljivanja; ID-jevi, nazivi i veze ruta poklapaju se sa XLSX uvozom. Oblik URL-a je `https://docs.google.com/spreadsheets/d/1V2hZb7GYw0beX2_1-_qV8q4DXk1qVMqU0cPJ2YKAqJc/export?format=csv&gid=<gid>`:

| Tab | gid |
| --- | --- |
| svi POI | 944979171 |
| staza-ukusa-bs | 0 |
| price-u-kamenu-bs | 1374632835 |
| izmedju-planina-i-neba-bs | 1806306056 |
| hercegovina-za-porodicu-bs | 301949931 |
| staza-vode-i-krsa-bs | 1724113342 |

Promene objavljenih CSV podataka ulaze u naredno uspešno preuzimanje endpointa. Šest tabova preuzima se odvojeno; ako su tokom uređivanja međusobno neusaglašeni, zahtev vraća 503 i može se ponoviti po završetku izmene.

Isti CSV izvor koristi i bot kroz alat `get_tourism_catalogue`, dostupan i za običan i za streaming razgovor. Za osnovne POI podatke i pripadnost rutama model preuzima aktuelni katalog, sa nazivima pet postojećih ruta, i bira relevantne zapise prema pitanju, istoriji i filterima. To ne zahteva promenu frontenda ni unos podataka u novu bazu. Opisi ostaju u postojećoj pretrazi znanja; katalog ne potvrđuje redosled obilaska, udaljenost ili trajanje. Ako CSV nije dostupan, alat vraća jasnu grešku i model treba da navede da aktuelni spisak nije mogao da proveri.

Događaje bot preuzima kroz `get_tourism_events`, koristeći isti WordPress adapter kao partnerski izvoz. Alat vraća nazive, opise, datume, opštine i linkove, uz `as_of_date` (trenutni UTC datum). Podrazumevano vraća samo događaje čiji kraj (ili početak kada kraj nedostaje) nije pre tog datuma; nedatirani zapisi se tada izostavljaju. Parametar `include_past=true` služi samo za izričito tražene prošle događaje/datume. Partnerski izvoz i dalje sadrži sve objavljene događaje. Model bira prema pitanju i filterima putovanja. Nedostupan izvor vraća grešku bez stare rezervne liste. Tačna satnica, adresa/GPS i organizator/kontakt čekaju izlaganje tih polja na WordPress-u; ne zaključuju se iz ID-ja lokacije ili nepotpunog vremena početka.

Model dobija aktuelni UTC datum pre prvog izbora alata. Ako poslednje pitanje navodi prošlu godinu, backend uključuje i istorijske zapise čak i kada model greškom pošalje `include_past=false`. Godine iz ranijih poruka ne aktiviraju tu zaštitu pri novom pitanju za predstojeće događaje.

ID-jevi su `poi_<ID iz glavne kartice>`, npr. `poi_1`; nasleđeni frontend `poi-*` ID-jevi zamenjeni su novim katalogom. Potpuna sinhronizacija treba da zameni stari skup, a ne da mu doda duplikate. Vlasnik tabele treba da sačuva dodeljene brojeve kada se redovi dodaju ili preuređuju; izvorna tabela većinu ID-jeva računa formulama. Backend čuva dodeljeni broj i ne prepoznaje samostalno da je neki POI prebrojan.

## Pokretanje lokalno

U direktorijumu backend repozitorijuma, uz instalirane `requirements.txt` i ODBC Driver 18:

```powershell
$env:INTEGRATION_KEY_SECRET = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Postojeće promenljive `MSSQL_HOST`, `MSSQL_DB`, `MSSQL_USER` i `MSSQL_PASS` moraju biti dostupne procesu. Koristi se postojeća `_sql_connection()` i `APP_ID` (podrazumevano 66). KPI ne koristi demo statistiku i ne upisuje podatke u SQL.

Lokalni base URL je `http://127.0.0.1:8000`. OpenAPI je na `/docs`, gde dugme **Authorize** prima partnerski API ključ preuzet jednokratnim linkom. Gornja promenljiva je interna serverska tajna; ne šalje se partnerima niti služi za autentifikaciju API poziva.

Produkcioni base URL je `https://smartherz-be.livelyflower-1c718338.eastus.azurecontainerapps.io`. Interna tajna čuva se kao Azure secret vezan za `INTEGRATION_KEY_SECRET`. Svakim partnerskim ključem upravlja postojeći AdminApp registar u MSSQL-u. AgroTour ključ nije ključ za ove endpoint-e.

## Autentifikacija i konfiguracija

Oba endpointa traže zaglavlje:

```http
X-API-Key: <integration-api-key>
```

| Promenljiva | Značenje |
| --- | --- |
| `INTEGRATION_KEY_SECRET` | Interna tajna za generisanje/predaju partnerskih ključeva; najmanje 32 nasumična znaka. Čuvati trajno i jednako na svim replikama. |
| `INTEGRATION_EVENTS_URL` | Opcionalno; podrazumevano `https://smart-herz.modoos.rs/wp-json/wp/v2/mec-events`. |

Nedostajući, nepoznat, istekao ili ukinut ključ, kao i ključ bez integration:read dozvole, vraća 401. Svaki API poziv proverava AdminApp registar u SQL-u, bez keširanja. Nedostupna baza vraća 503. Prethodni zajednički `INTEGRATION_API_KEY` više se ne prihvata. SMART ERA potvrda načina autentifikacije potrebna je pre povezivanja.

## Upravljanje ključevima i jednokratna predaja

Ključevi se kreiraju, rotiraju i opozivaju u **AdminApp → Administracija → API ključevi**,
za klijenta **18** i aplikaciju **SMART-HERZ (66)**. Korisnik mora imati administratorski grant
za tu kombinaciju klijenta i aplikacije. Naziv ključa može biti naziv partnera.

1. **Kreiraj ključ** otvara dijalog sa jednokratnim linkom, PIN-om i rokom. Kopirajte ih/preuzmite
   i pošaljite ručno partneru. Za SMART-HERZ se sam ključ ne prikazuje u AdminApp-u.
2. **Novi link** daje dodatni jednokratni link za isti ključ.
3. **Revoke / Opozovi** odmah blokira samo taj ključ i sve njegove neiskorišćene linkove.
4. **Rotate / Rotiraj** izdaje novi ključ i link, a stari ključ i stari linkovi prestaju da važe.

Jedina evidencija prava je `admin.AdminApiKeys`, uz aktivan `integration:read` scope iz
`admin.AdminApiKeyScopes` i `admin.AdminApiKeyScopeAssignments`. Svaki API poziv proverava
aplikaciju 66, status, opoziv, istek i dozvolu u SQL-u, bez keširanja. Nema rezervnog zajedničkog
ključa niti dozvole koja se dobija samo postojanjem zapisa u `devplay`.

Postojeće `devplay.SmartHerzPartners` zapise koristimo samo kao metapodatke za predaju ključa
(nasumični identifikator i hash); njihovo polje `revoked_at` više ne odlučuje o pristupu.
`devplay.SmartHerzKeyLinks` zadržava hash tokena, PIN proveru, hash ključa, istek i broj pokušaja.
Postojeći SMART ERA ključ prenosi se u AdminApp registar sa istim hashom, a raniji linkovi ostaju
važeći do iskorišćavanja/isteka, ako sam ključ nije opozvan ili istekao.

Interna tajna `INTEGRATION_KEY_SECRET` u SMART-HERZ-u mora biti jednaka AdminApp tajni
`SMARTHERZ_INTEGRATION_KEY_SECRET`. Čuvaju se kao Azure secrets. HMAC-SHA256 iz te tajne i
nasumičnog identifikatora omogućava predaju istog ključa bez čuvanja čitljivih ključeva u SQL-u.
Nema novih servisa ni tabela; SQL pooling ostaje nepromenjen. Za novu instalaciju postojeće
`devplay` tabele kreiraju skripte 001/002 iz ovog repozitorijuma, a scope registruje
`adminapp/sql/002_smartherz_key_delivery.sql`. Pre deploy-a SQL administrator izvršava
`sql/003_adminapp_registry_permissions.sql`: postojećem backend nalogu dodaje samo SELECT
nad tri AdminApp tabele. Bez tih prava provera ključeva vraća 503. Zahtevi aplikacije ne izvršavaju DDL.

- Link podrazumevano važi sedam dana, najduže do isteka ključa. Token je u URL fragmentu `#...`.
  Samo otvaranje stranice ne troši link.
- `POST /api/v1/integration/key-links/redeem` proverava token i PIN. Uspeh vraća ključ jednom.
  Pogrešan PIN vraća 403; posle pet pogrešnih pokušaja link se poništava. Iskorišćen, istekao ili
  opozvan link vraća 410. SQL/konfiguraciona greška vraća 503, bez vraćanja ključa.
- `UPDLOCK, HOLDLOCK` i commit pre odgovora čuvaju jednokratnost između različitih procesa/replika.
  Stranica i odgovori sa ključem koriste `Cache-Control: no-store`.
- Ključ važi posle iskorišćavanja linka, do opoziva/isteka. Zahtev već odobren pre opoziva može
  završiti odgovor. Drugi ključevi nastavljaju da rade.

Za operatora je opciono ostala komanda za novi link postojećeg AdminApp ključa, uz istu tajnu i bazu:

```powershell
python integration_key_links.py --key-id <api_key_id-iz-adminapp> --base-url https://smartherz-be.livelyflower-1c718338.eastus.azurecontainerapps.io
```

Stare `create/link/revoke --partner` komande su zamenjene AdminApp upravljanjem.

## Turistički podaci

`GET /api/v1/integration/tourism`

Odgovor sadrži `generated_at` (UTC), `places`, `routes` i `events`. Na proveri 20.10.2026. `places` sadrži osam destinacija i 281 POI (ukupno 289), a `routes` pet ruta sa ukupno 481 jedinstvenom vezom ruta–POI. WordPress ID-jevi imaju prefikse `destination_`, `route_` i `event_`; POI-jevi koriste `poi_<ID iz glavne kartice>`. Zaglavlje `X-SmartHerz-Data-Mode: live-csv-live-wordpress` označava žive CSV i WordPress izvore. Nema demo POI zapisa.

Osnovna polja zapisa su `id`, `name`, opciono `description` i `url`. `places` ima i `category` (`destination` za opštine) i opcioni `slug`. Uvezeni POI-jevi imaju `municipality`, izvorni naziv opštine (za dve planine to je tekst sa dve opštine). `routes[].poi_ids` referiše na ID-jeve u `places`. To je pripadnost tematskoj ruti, ne potvrđen itinerer ili geometrija staze. Događaji imaju dostupne `start_date` i `end_date` u formatu `YYYY-MM-DD`; to su lokalni datumi portala `Europe/Sarajevo`, bez izmišljanja vremena početka. HTML naslova/opisa pretvara se u običan tekst. Nepopunjena tourism polja se izostavljaju.

Događaji dodatno imaju `municipality`, mapiran iz `mec_category` preko javnog `/wp-json/wp/v2/mec_category`. Nepopunjena kategorija izostavlja to polje; više opština navodi se jednom, odvojeno zarezom. Nepoznata referenca kategorije vraća grešku izvora, umesto pogrešnog naziva. Taksonomija se preuzima samo kada događaji imaju dodeljene kategorije.

Skraćen primer:

```json
{
  "generated_at": "2026-10-21T12:00:00Z",
  "places": [
    {"id": "destination_347", "name": "Trebinje", "category": "destination", "slug": "trebinje", "url": "https://smart-herz.modoos.rs/destinacije/trebinje/", "updated_at": "2026-10-20T21:54:05Z"},
    {"id": "poi_1", "name": "Manastir Tvrdoš", "category": "poi", "municipality": "Trebinje"}
  ],
  "routes": [
    {"id": "route_1590", "name": "Staza ukusa – Istočna Hercegovina", "url": "https://smart-herz.modoos.rs/tematske-rute/staza-ukusa-istocna-hercegovina/", "poi_ids": ["poi_1", "poi_2", "poi_3", "poi_4", "poi_5", "poi_6"]}
  ],
  "events": [
    {"id": "event_2844", "name": "Memorijalni turnir u malom fudbalu „Mišo Tamindžija“", "url": "https://smart-herz.modoos.rs/events/memorijalni-turnir-u-malom-fudbalu-miso-tamindzija/", "start_date": "2026-12-16", "end_date": "2027-01-06", "municipality": "Nevesinje"}
  ]
}
```

Svaki poziv preuzima svih šest CSV tabova, osnovne podatke 13 poznatih WordPress stranica destinacija/ruta i sve stranice događaja. Proverava broj WordPress zapisa prema `X-WP-Total`, jedinstvenost ID-jeva i prisustvo svih očekivanih destinacija/ruta. Neuspešno preuzimanje, nepotpun ili neispravan izvor vraća 503, bez delimičnog uspešnog izvoza. Prazna lista događaja je dozvoljena kada API uspešno potvrdi da ih nema. Prazan glavni POI tab, ponovljeni POI ID-jevi ili nepostojeći POI u listi rute takođe vraćaju 503. Ponovljeno pojavljivanje istog POI-ja u istoj tematskoj ruti spaja se u jednu vezu.

SMART ERA planira polling jednom dnevno i potpunu sinhronizaciju dogovorenog skupa. Zato svaki poziv automatski čita aktuelne dostupne CSV podatke. Nema posebnog pozadinskog posla. Geometrija ruta, redosled obilaska i opisi koji nisu usaglašeni ne izmišljaju se.

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

SQL provera pre ove izmene pokazala je 42 stare sesije, poslednju od 21.08.2026. Nova verzija nakon uspešnog običnog ili streaming odgovora upisuje jednu anonimnu sesiju u postojeću `chat.Threads`, a pri sledećim odgovorima ažurira `last_message_at`. Sesija koristi postojeći jednosatni rok neaktivnosti; novi objekat sesije dobija nov nasumičan SQL ID. `created_at` je vreme prvog uspešnog odgovora u UTC. Istorijski podaci ostaju netaknuti i raniji razgovori koji nisu evidentirani ne mogu se naknadno izračunati. Ne čuvaju se tekst razgovora, lični podaci ili identifikatori iz browsera.

Backend nalog već ima SELECT, INSERT i UPDATE prava nad ovom tabelom; nisu potrebne nove tabele ili DDL. Greška upisa se beleži u serverski log i ne prekida uspešan odgovor korisniku. `closed_at` se ne popunjava procenom iz poslednje aktivnosti; prosečno trajanje i broj preporuka ostaju `null` dok ti podaci nemaju zasebno, dogovoreno merenje.

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

Ovi dodatni testovi kreiraju privremene ključeve/scope veze u AdminApp registru i metapodatke/linkove u dve `devplay.SmartHerz*` tabele; čiste samo svoje zapise nakon provere. Pokrivaju odvojene ključeve, pojedinačno ukidanje, ponovno izdavanje, PIN, istek, pogrešnu internu tajnu, ponovni pristup, novi proces i istovremene zahteve. Kreiranje linka, kao i u redovnom radu, uklanja istekle linkove.

Statusi: 200 uspešan odgovor; 401 autentifikacija; 422 datumi; 503 konfiguracija ili izvor nisu dostupni.

## Produkciona konfiguracija

AgroTour koristi `AGROTOUR_SEARCH_URL=https://agrotour.eu/api/v1/search` i postojeći zaseban `AGROTOUR_API_KEY`. Taj ključ nije integration ključ za SMART ERA.

`CORS_ORIGINS` i Azure ingress CORS dozvoljavaju Storage frontend origin i stvarni Front Door origin `https://positivefrontend-erauhbdtd7c7dzed.z02.azurefd.net`. SQL pooling ostaje uključen u `database.py` i Docker unixODBC konfiguraciji; ova integracija ga ne menja.
