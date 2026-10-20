"""Read basic POI identities and thematic-route membership from public CSV tabs."""
import csv
import io
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen

CSV_BASE = "https://docs.google.com/spreadsheets/d/1V2hZb7GYw0beX2_1-_qV8q4DXk1qVMqU0cPJ2YKAqJc/export?format=csv&gid="
POI_GID = "944979171"
ROUTE_GIDS = {
    "route_1590": "0",
    "route_1699": "1374632835",
    "route_1968": "1806306056",
    "route_2010": "301949931",
    "route_2144": "1724113342",
}


def _text(value: str) -> str:
    return " ".join(value.split())


def _poi_id(value: str) -> str:
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError("Invalid POI ID.") from None
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError("POI ID must be a positive integer.")
    return f"poi_{int(number)}"


def _fetch_csv_rows(gid: str) -> list[tuple[str, str, str]]:
    request = Request(CSV_BASE + gid, headers={"Accept": "text/csv", "User-Agent": "SmartHerz-integration/1.0"})
    with urlopen(request, timeout=20) as response:
        reader = csv.reader(io.StringIO(response.read().decode("utf-8-sig")), strict=True)
        headers = next(reader, [])
        if [_text(value).casefold() for value in headers[:3]] != ["id", "naziv", "opstina"]:
            raise ValueError("Unexpected CSV identity columns.")
        records = []
        for row in reader:
            # Ignore empty rows and spreadsheet formula/formatting tails in other columns.
            if not any(value.strip() for value in row[:3]):
                continue
            if len(row) < 3:
                raise ValueError("Incomplete CSV identity row.")
            records.append(tuple(_text(value) for value in row[:3]))
        return records


def fetch_tourism_catalogue() -> dict:
    """Download all six tabs on each export; never fall back to an old snapshot."""
    places = {}
    for value, name, municipality in _fetch_csv_rows(POI_GID):
        poi_id = _poi_id(value)
        if poi_id in places or not name or not municipality:
            raise ValueError("Duplicate ID or missing POI name/municipality.")
        places[poi_id] = {"id": poi_id, "name": name, "category": "poi", "municipality": municipality}
    if not places:
        raise ValueError("The POI catalogue is empty.")

    routes = {}
    for route_id, gid in ROUTE_GIDS.items():
        members = []
        for value, name, municipality in _fetch_csv_rows(gid):
            poi_id = _poi_id(value)
            canonical = places.get(poi_id)
            if canonical is None or name.casefold() != canonical["name"].casefold():
                raise ValueError("Unknown route POI or inconsistent name.")
            listed = {part.strip().casefold() for part in municipality.split(",") if part.strip()}
            actual = {part.strip().casefold() for part in canonical["municipality"].split(",")}
            if not listed or not listed <= actual:
                raise ValueError("Inconsistent route POI municipality.")
            if poi_id not in members:
                members.append(poi_id)
        routes[route_id] = members
    return {"places": list(places.values()), "route_poi_ids": routes}
