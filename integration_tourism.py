"""Read basic POI identities and thematic-route membership from public CSV tabs."""
import csv
import io
from collections import defaultdict
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

# This published route entry is present in the chat workbook but has no
# canonical ID in the live POI master. Reconcile it normally once one exists.
ROUTE_ONLY_POI = ("route_1699", "crkva svete trojice na gorici", "berkovići")


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


def _fetch_csv_rows(gid: str) -> list[dict[str, str]]:
    request = Request(CSV_BASE + gid, headers={"Accept": "text/csv", "User-Agent": "SmartHerz-integration/1.0"})
    with urlopen(request, timeout=20) as response:
        reader = csv.reader(io.StringIO(response.read().decode("utf-8-sig")), strict=True)
        headers = [_text(value).casefold() for value in next(reader, [])]
        if headers[:3] != ["id", "naziv", "opstina"]:
            raise ValueError("Unexpected CSV identity columns.")
        records = []
        for row in reader:
            # Ignore empty rows and spreadsheet formula/formatting tails in other columns.
            if not any(value.strip() for value in row[:3]):
                continue
            if len(row) < 3:
                raise ValueError("Incomplete CSV identity row.")
            if gid == POI_GID and not any(value.strip() for value in row[1:]):
                # The sheet can retain an ID formula after the POI cells are cleared.
                continue
            records.append({header: _text(value) for header, value in zip(headers, row) if header})
        return records


def _municipalities(value: str) -> set[str]:
    return {part.strip().casefold() for part in value.split(",") if part.strip()}


def fetch_tourism_catalogue() -> dict:
    """Download all six tabs on each export; never fall back to an old snapshot."""
    places = {}
    names = defaultdict(list)
    images = defaultdict(list)
    for row in _fetch_csv_rows(POI_GID):
        poi_id = _poi_id(row["id"])
        name, municipality = row["naziv"], row["opstina"]
        if poi_id in places or not name or not _municipalities(municipality):
            raise ValueError("Duplicate ID or missing POI name/municipality.")
        places[poi_id] = {"id": poi_id, "name": name, "category": "poi", "municipality": municipality}
        names[name.casefold()].append(poi_id)
        if image := row.get("thumbnail_url"):
            images[image].append(poi_id)
    if not places:
        raise ValueError("The POI catalogue is empty.")

    routes = {}
    for route_id, gid in ROUTE_GIDS.items():
        members = []
        for row in _fetch_csv_rows(gid):
            name, municipality = row["naziv"].casefold(), row["opstina"].casefold()
            listed = _municipalities(municipality)
            if not name or not listed:
                raise ValueError("Incomplete route POI identity.")
            raw_id = _poi_id(row["id"]) if row["id"] else None
            matches = [poi_id for poi_id in names.get(name, ()) if listed <= _municipalities(places[poi_id]["municipality"])]
            image_matches = images.get(row.get("thumbnail_url"), [])
            local_business = row.get("kategorija", "").casefold() == "lokalni biznis" and row.get("tip", "").casefold() == "biznis"
            if raw_id in matches:
                members.append(raw_id)
            elif len(matches) == 1:
                # Route sheets can use their own numbering; retain the master ID.
                members.append(matches[0])
            elif (not matches and not local_business and raw_id in places and image_matches == [raw_id]
                  and listed <= _municipalities(places[raw_id]["municipality"])):
                # Same master ID, municipality and unique image corroborate a title variant.
                members.append(raw_id)
            elif not matches and (
                local_business
                or (raw_id is None and (route_id, name, municipality) == ROUTE_ONLY_POI)
            ):
                continue
            else:
                raise ValueError("Unknown or ambiguous route POI identity.")
        routes[route_id] = list(dict.fromkeys(members))
    return {"places": list(places.values()), "route_poi_ids": routes}
