"""Serve the public, reviewed projection of the Smart ERA workbook to chat."""

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from agrotour_context import excerpt
from chat_recommendations import normalized_words

SNAPSHOT_PATH = Path(__file__).with_name("data") / "tourism_workbook.json"
DETAIL_LIMIT = 8
ROUTE_TERMS = {
    "route_1590": ("staz", "ukusa"),
    "route_1699": ("uklesan",),
    "route_1968": ("planina", "neba"),
    "route_2010": ("porodic",),
    "route_2144": ("vode", "krsa"),
}


@lru_cache(maxsize=1)
def load_workbook_catalogue() -> dict:
    with SNAPSHOT_PATH.open(encoding="utf-8") as snapshot:
        return json.load(snapshot)


def _compact(record: dict, *fields: str) -> dict:
    return {field: record[field] for field in fields if field in record}


def _details(records: list[dict], query: str, *fields: str) -> list[dict]:
    """Keep the complete index, and include richer detail for likely matches."""
    normalized = normalized_words(query)
    terms = {word[:5] for word in normalized.split() if len(word) >= 4}
    ranked = []
    for record in records:
        name = normalized_words(record["name"])
        kinds = " ".join(record.get("categories", ())) or record.get("type", "")
        # Prefixes accommodate common inflections; the model still checks relevance.
        name_words = {word[:5] for word in name.split()}
        kind_words = {word[:5] for word in normalized_words(kinds).split()}
        town_words = {word[:5] for word in normalized_words(record["municipality"]).split()}
        score = (
            100 * (name in normalized)
            + 4 * len(terms & name_words)
            + 2 * len(terms & kind_words)
            + len(terms & town_words)
        )
        if score and any(record.get(field) for field in fields):
            ranked.append((score, record))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in ranked[:DETAIL_LIMIT]]


def _with_google_maps_url(record: dict) -> dict:
    coordinates = record.get("coordinates")
    if not coordinates:
        return record
    params = urlencode({"api": 1, "query": f"{coordinates['lat']},{coordinates['lon']}"})
    return {**record, "google_maps_url": f"https://www.google.com/maps/search/?{params}"}


def workbook_map_links(query: str) -> list[dict]:
    snapshot = load_workbook_catalogue()
    text = normalized_words(query)
    return [
        _compact(_with_google_maps_url(record), "name", "municipality", "google_maps_url")
        for group in ("places", "businesses") for record in snapshot[group]
        if record.get("coordinates") and normalized_words(record["name"]) in text
    ]


def _place_with_routes(place: dict, routes: list[dict]) -> dict:
    return {**place, "route_names": [route["name"] for route in routes if place["id"] in route["poi_ids"]]}


def workbook_named_places(query: str) -> list[dict]:
    """Find named POIs, including an inflected first word such as Tvrđave Strač."""
    snapshot = load_workbook_catalogue()
    text = normalized_words(query)
    matches = []
    for place in snapshot["places"]:
        name = normalized_words(place["name"]).strip()
        first, _, rest = name.partition(" ")
        if not rest:
            continue  # A single word can also be a common noun or a broad topic.
        prefix = re.escape(first[:5]) + r"\w*" if len(first) > 5 else re.escape(first)
        if re.search(r"\b" + prefix + " " + re.escape(rest) + r"\b", text):
            matches.append(place)
    return [_place_with_routes(_with_google_maps_url(place), snapshot["routes"])
            for place in matches[:DETAIL_LIMIT]]


def workbook_tourism_catalogue(query: str, focus: str | None = None) -> dict:
    snapshot = load_workbook_catalogue()
    places = snapshot["places"]
    businesses = snapshot["businesses"]
    routes = snapshot["routes"]
    search = f"{focus or ''} {query}"
    words = normalized_words(search).split()
    selected_routes = [
        route for route in routes if all(any(word.startswith(term) for word in words)
                                         for term in ROUTE_TERMS.get(route["id"], (route["id"],)))
    ]
    detailed = _details(places, search, "description", "coordinates")
    named = workbook_named_places(search)
    detailed.extend(named)
    # A membership question about one specific POI needs its route names, not
    # the other members. Keep full route membership for an explicit list.
    if len(named) == 1 and not re.search(r"\b(?:koji su|sve|svih|all|list|spisak|nabroj\w*|izlist\w*)\b",
                                         normalized_words(query)):
        selected_routes = []
    place_ids = {place["id"] for place in detailed}
    for route in selected_routes:
        place_ids.update(route["poi_ids"])
    # Municipality prefixes handle forms like Trebinju, Bileći and Kalinoviku.
    terms = {word[:5] for word in words if len(word) >= 4}
    local_places = [place for place in places if terms & {
        word[:5] for word in normalized_words(place["municipality"]).split()
    }]
    topical_places = [place for place in places if terms & {
        word[:5] for word in normalized_words(
            " ".join([place["name"], *place.get("categories", [])])
        ).split()
    }]
    relevant_places = [place for place in places if place["id"] in place_ids]
    if not selected_routes:
        relevant_places.extend(place for place in (local_places or topical_places) if place["id"] not in place_ids)
    if not relevant_places and not selected_routes:
        # Keep the compact index searchable when the question has no recognizable focus.
        relevant_places = places
    organizations = snapshot["organizations"]
    org_details = {org["id"] for org in _details(organizations, search, "description")}
    business_ids = set(workbook_named_business_ids(search))
    route_ids = {route["id"] for route in selected_routes}
    if route_ids:
        business_ids.update(item["id"] for item in businesses if route_ids.intersection(item["route_ids"]))
    return {
        "source": "workbook",
        "places": [_compact(place, "id", "name", "category", "municipality", "categories")
                   for place in relevant_places],
        "routes": [
            {**route, "business_ids": [item["id"] for item in businesses if route["id"] in item["route_ids"]]}
            if route["id"] in route_ids else _compact(route, "id", "name")
            for route in routes
        ],
        "route_businesses": [_compact(item, "id", "name", "municipality")
                             for item in businesses if item["id"] in business_ids],
        "organizations": [org if org["id"] in org_details
                          else _compact(org, "id", "name", "municipality", "type") for org in organizations],
        "place_details": [_place_with_routes(place, routes)
                          for place in {item["id"]: item for item in detailed}.values()],
        "map_links": workbook_map_links(search),
    }


def workbook_business_index() -> list[dict]:
    return [_compact(item, "id", "name", "municipality", "type")
            for item in load_workbook_catalogue()["businesses"]]


def workbook_named_business_ids(query: str) -> list[str]:
    text = normalized_words(query)
    return [item["id"] for item in load_workbook_catalogue()["businesses"]
            if normalized_words(item["name"]) in text]


def workbook_business_fallback(*, business_ids: list[str] | None = None, query: str = "") -> dict:
    businesses = load_workbook_catalogue()["businesses"]
    if business_ids is not None:
        businesses = [item for item in businesses if item["id"] in business_ids]
    return {
        "source": "workbook",
        "businesses": [_compact(item, "id", "name", "municipality", "type") for item in businesses],
        "business_details": [
            {**_compact(item, "id", "name", "municipality", "type"),
             "description": excerpt(item["description"], query) if business_ids is None else item["description"]}
            for item in businesses if item.get("description")
        ],
    }
