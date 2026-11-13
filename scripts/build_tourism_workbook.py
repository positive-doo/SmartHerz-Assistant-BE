"""Project the workbook into the public chat catalogue (offline, requires openpyxl)."""
import argparse
from collections import defaultdict
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

from openpyxl import load_workbook


ROUTES = {
    "staza-ukusa": ("route_1590", "Staza ukusa – Istočna Hercegovina"),
    "price-u-kamenu": ("route_1699", "Priče uklesane u kamenu"),
    "izmedju-planina-i-neba": ("route_1968", "Između planina i neba"),
    "hercegovina-za-porodicu": ("route_2010", "Hercegovina za cijelu porodicu"),
    "staza-vode-i-krsa": ("route_2144", "Staza vode i krša"),
}
POI_COLUMNS = ("ID", "Naziv", "Opstina")
ROUTE_COLUMNS = (*POI_COLUMNS, "Kategorija", "Opis", "Thumbnail_URL", "Lat", "Lon", "Tip")
BUSINESS_COLUMNS = ("ID", "Naziv", "Opis", "Opština", "Lat", "Lon", "Slika_url", "Link", "Vrsta", "Ruta")
ORGANIZATION_COLUMNS = (
    "ID", "Naziv", "Opština", "Vrsta organizacije", "Oblast djelovanja", "Kratak opis", "Detaljan opis",
    "Mogućnosti za posjetioce", "Kontakt osoba", "Telefon", "E-mail", "Veb-sajt", "Instagram", "Facebook",
    "URL fotografije", "Opis fotografije", "Redoslijed prikaza", "Status objave", "Izvor", "Interna napomena",
    "Adresa", "Godina osnivanja", "Sekcije i povezane grupe", "Manifestacije i programi", "Dodatni kontakti",
)
DIRECT_CONTACT = re.compile(r"\b[\w.%+-]+@[\w.-]+\.\w{2,}\b|\+\d[\d\s()./-]{6,}\d|\b(?:https?://|www\.)", re.I)
# These reviewed records exist only in route tabs. IDs do not depend on their row number
# or the route's local numbering, which collides with the POI master.
ROUTE_ONLY_PLACES = {
    ("price-u-kamenu", "crkva svete trojice na gorici", "berkovići"): "poi_workbook_sveta_trojica_gorica",
}
ROUTE_ONLY_BUSINESSES = {
    ("staza-ukusa", "mljekara pađeni", "bileća"): "business_route_mljekara_padjeni",
}
POI_CATEGORIES = {"Avantura", "Gastro", "Kultura", "Lokalni biznis", "Porodica", "Priroda"}


def text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def key(value):
    return unicodedata.normalize("NFKC", text(value)).casefold()


def public_text(value):
    # A public listing can still contain a direct contact in its prose.
    return " ".join(part for part in re.split(r"(?<=[.!?])\s+", text(value)) if not DIRECT_CONTACT.search(part))


def number(value):
    if type(value) in (int, float) and math.isfinite(value) and value > 0 and int(value) == value:
        return int(value)
    return None


def municipalities(value):
    return {key(part) for part in text(value).split(",") if text(part)}


def rows(book, sheet, columns):
    values = book[sheet].iter_rows(max_col=len(columns), values_only=True)
    if tuple(text(value) for value in next(values, ())) != columns:
        raise ValueError(f"Unexpected headers in {sheet}.")
    return enumerate(values, 2)


def warn(sheet, line, message):
    print(f"{sheet}:{line}: {message}", file=sys.stderr)


def put(records, id_, record, where):
    if not id_ or id_ in records or not record["name"] or not record["municipality"]:
        raise ValueError(f"Missing or duplicate identity in {where}.")
    if any(DIRECT_CONTACT.search(record[field]) for field in ("name", "municipality")):
        raise ValueError(f"Unexpected link or contact in identity at {where}.")
    records[id_] = {"id": id_, **record}


def coordinates(lat, lon):
    # A deliberately broad bound for Eastern Herzegovina catches swapped or misentered points.
    if type(lat) in (int, float) and type(lon) in (int, float) and 42 <= lat <= 44 and 17 <= lon <= 20:
        return lat, lon
    return None


def build_catalogue(source):
    data = source.read_bytes()
    book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        return {"source": {"file": source.name, "sha256": hashlib.sha256(data).hexdigest()}, **project(book)}
    finally:
        book.close()


def project(book):
    expected = {"svi POI", "lokalni-biznisi", "KUDovi i drustva", *(f"{route}-bs" for route in ROUTES)}
    if set(book.sheetnames) != expected:
        raise ValueError("Unexpected workbook sheets; review the mapping before importing.")

    places = {}
    names = defaultdict(list)
    for line, (raw_id, name, municipality) in rows(book, "svi POI", POI_COLUMNS):
        if not text(name) and not text(municipality):
            if raw_id is not None:
                warn("svi POI", line, "ignored ID without name or municipality")
            continue
        poi_id = f"poi_{number(raw_id)}" if number(raw_id) else None
        put(places, poi_id, {"name": text(name), "category": "poi", "municipality": text(municipality)}, f"svi POI:{line}")
        names[key(name)].append(poi_id)

    route_rows = {slug: list(rows(book, f"{slug}-bs", ROUTE_COLUMNS)) for slug in ROUTES}
    businesses = {}
    business_names = {}
    for line, row in rows(book, "lokalni-biznisi", BUSINESS_COLUMNS):
        raw_id, name, description, municipality, lat, lon, _, _, kind, routes = row
        if not text(name):
            if any(text(value) for value in row[2:]):
                warn("lokalni-biznisi", line, "ignored row without a business name")
            continue
        if text(name).startswith(("https://", "http://")):
            # In this workbook a business name was replaced with its picture URL. Recover
            # it only if an exact image, description and municipality in a route identify it.
            corroborated = {
                text(route_row[1]) for route in route_rows.values() for _, route_row in route
                if key(route_row[3]) == "lokalni biznis" and key(route_row[2]) == key(municipality)
                and text(route_row[5]) == text(name) and text(description)
                and text(route_row[4]) == text(description) and text(route_row[1])
            }
            if len(corroborated) != 1:
                raise ValueError(f"Cannot recover the business name at lokalni-biznisi:{line}.")
            name = next(iter(corroborated))
            warn("lokalni-biznisi", line, "recovered business name from matching route description and image")
        route_ids = []
        for slug in text(routes).split(","):
            if not slug.strip():
                continue
            if slug.strip() not in ROUTES:
                raise ValueError(f"Unknown business route at lokalni-biznisi:{line}.")
            route_ids.append(ROUTES[slug.strip()][0])
        business_id = f"business_{number(raw_id)}" if number(raw_id) else None
        record = {"name": text(name), "municipality": text(municipality), "type": text(kind)}
        if coordinates(lat, lon):
            record["coordinates"] = {"lat": lat, "lon": lon}
        record.update(description=public_text(description), route_ids=list(dict.fromkeys(route_ids)))
        put(businesses, business_id, record, f"lokalni-biznisi:{line}")
        business_key = (key(name), key(municipality))
        if business_key in business_names:
            raise ValueError(f"Duplicate business name and municipality at lokalni-biznisi:{line}.")
        business_names[business_key] = business_id

    routes = {route_id: {"id": route_id, "name": name, "poi_ids": []} for route_id, name in ROUTES.values()}
    references = defaultdict(list)
    pending = []
    for slug, (route_id, _) in ROUTES.items():
        sheet = f"{slug}-bs"
        for line, row in route_rows[slug]:
            raw_id, name, municipality, category, *_ = row
            if not text(name) and not text(municipality):
                continue
            listed = municipalities(municipality)
            candidates = [p for p in names[key(name)] if listed and listed <= municipalities(places[p]["municipality"])]
            raw_poi_id = f"poi_{number(raw_id)}"
            route_key = (slug, key(name), key(municipality))
            if raw_poi_id in candidates:
                poi_id = raw_poi_id
            elif len(candidates) == 1:
                poi_id = candidates[0]
                warn(sheet, line, f"used {poi_id} from matching name and municipality rather than route row ID")
            elif route_key in ROUTE_ONLY_PLACES:
                poi_id = ROUTE_ONLY_PLACES[route_key]
                put(places, poi_id, {"name": text(name), "category": "poi", "municipality": text(municipality)}, f"{sheet}:{line}")
                names[key(name)].append(poi_id)
                warn(sheet, line, "included verified route-only POI with its own ID")
            else:
                poi_id = None
            if poi_id:
                routes[route_id]["poi_ids"].append(poi_id)
                references[poi_id].append(row)
                if key(category) != "lokalni biznis":
                    continue
            if key(category) == "lokalni biznis":
                business_id = business_names.get((key(name), key(municipality)))
                if business_id is None and route_key in ROUTE_ONLY_BUSINESSES:
                    business_id = ROUTE_ONLY_BUSINESSES[route_key]
                    record = {"name": text(name), "municipality": text(municipality), "type": text(category)}
                    lat, lon = row[6:8]
                    if coordinates(lat, lon):
                        record["coordinates"] = {"lat": lat, "lon": lon}
                    record.update(description=public_text(row[4]), route_ids=[route_id])
                    put(businesses, business_id, record, f"{sheet}:{line}")
                    business_names[key(name), key(municipality)] = business_id
                    warn(sheet, line, "included verified route-only business with its own ID")
                if business_id is None:
                    warn(sheet, line, "ignored a business absent from the business tab")
                elif route_id not in businesses[business_id]["route_ids"]:
                    businesses[business_id]["route_ids"].append(route_id)
                    warn(sheet, line, "added membership explicit in this route tab and absent from the business tab")
            else:
                pending.append((sheet, line, route_id, raw_poi_id, row))

    for sheet, line, route_id, poi_id, row in pending:
        # A title variant can be resolved if the same numeric ID and municipality also
        # share a nonempty description and image with a verified identity in another tab.
        if text(row[4]) and text(row[5]) and any(
            key(other[2]) == key(row[2]) and text(other[4]) == text(row[4]) and text(other[5]) == text(row[5])
            for other in references.get(poi_id, ())
        ):
            routes[route_id]["poi_ids"].append(poi_id)
            references[poi_id].append(row)
            warn(sheet, line, f"recognized title variant for {poi_id} from matching description and image")
        else:
            warn(sheet, line, "ignored a route row without a verified POI identity")

    for poi_id, place in places.items():
        variants = references[poi_id]
        categories = sorted({text(row[3]) for row in variants} & POI_CATEGORIES, key=str.casefold)
        if categories:
            place["categories"] = categories
        descriptions = sorted({public_text(row[4]) for row in variants} - {""}, key=lambda value: (-len(value), value))
        if descriptions and all(descriptions[0].casefold().startswith(value.casefold()) for value in descriptions[1:]):
            place["description"] = descriptions[0]
        points = {coordinates(*row[6:8]) for row in variants if row[6] is not None or row[7] is not None}
        if len(points) == 1 and None not in points:
            lat, lon = next(iter(points))
            place["coordinates"] = {"lat": lat, "lon": lon}
    for route in routes.values():
        route["poi_ids"] = list(dict.fromkeys(route["poi_ids"]))

    organizations = {}
    public_columns = {4: "activities", 7: "visitor_options", 20: "address", 21: "founded", 22: "sections", 23: "programs"}
    for line, row in rows(book, "KUDovi i drustva", ORGANIZATION_COLUMNS):
        if not text(row[1]):
            continue
        if key(row[17]) != "objavljeno":
            warn("KUDovi i drustva", line, "ignored a listing not marked Objavljeno")
            continue
        record = {"name": text(row[1]), "municipality": text(row[2]), "type": text(row[3])}
        description = public_text(row[6]) or public_text(row[5])
        if description:
            record["description"] = description
        for column, field in public_columns.items():
            value = public_text(str(row[column])) if row[column] is not None else ""
            if value:
                record[field] = value
        put(organizations, text(row[0]), record, f"KUDovi i drustva:{line}")

    if not all((places, businesses, organizations)) or not all(route["poi_ids"] for route in routes.values()):
        raise ValueError("The workbook projection unexpectedly contains an empty catalogue or route.")
    return {"places": list(places.values()), "routes": list(routes.values()),
            "businesses": list(businesses.values()), "organizations": list(organizations.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    catalogue = build_catalogue(args.workbook)
    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(", ".join(f"{name}: {len(catalogue[name])}" for name in ("places", "routes", "businesses", "organizations")))


if __name__ == "__main__":
    main()
