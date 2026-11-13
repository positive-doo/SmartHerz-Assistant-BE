"""Serve the public, reviewed projection of the Smart ERA workbook to chat."""

import json
import re
from functools import lru_cache
from pathlib import Path

from chat_recommendations import normalized_words

SNAPSHOT_PATH = Path(__file__).with_name("data") / "tourism_workbook.json"
DETAIL_LIMIT = 8


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
    return [_place_with_routes(place, snapshot["routes"]) for place in matches[:DETAIL_LIMIT]]


def workbook_tourism_catalogue(query: str, focus: str | None = None) -> dict:
    snapshot = load_workbook_catalogue()
    places = snapshot["places"]
    businesses = snapshot["businesses"]
    return {
        "source": "workbook",
        "places": [_compact(place, "id", "name", "category", "municipality", "categories") for place in places],
        "routes": [
            {**route, "business_ids": [item["id"] for item in businesses if route["id"] in item["route_ids"]]}
            for route in snapshot["routes"]
        ],
        "route_businesses": [_compact(item, "id", "name", "municipality") for item in businesses if item["route_ids"]],
        "organizations": snapshot["organizations"],
        "place_details": [_place_with_routes(place, snapshot["routes"])
                          for place in _details(places, f"{focus or ''} {query}", "description", "coordinates")],
    }


def workbook_business_index() -> list[dict]:
    return [_compact(item, "id", "name", "municipality", "type")
            for item in load_workbook_catalogue()["businesses"]]


def workbook_business_fallback(*, business_ids: list[str] | None = None) -> dict:
    businesses = load_workbook_catalogue()["businesses"]
    if business_ids is not None:
        businesses = [item for item in businesses if item["id"] in business_ids]
    return {
        "source": "workbook",
        "businesses": [_compact(item, "id", "name", "municipality", "type") for item in businesses],
        "business_details": [item for item in businesses if item.get("description")],
    }
