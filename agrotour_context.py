"""Keep search evidence separate from the details sent to answer synthesis."""

import re

from chat_recommendations import normalized_words

_BUSINESS_FIELDS = (
    "business_name", "municipality", "type", "types", "offerings", "street_address",
    "latitude", "longitude", "website", "phone", "contact_email",
)


def excerpt(text: str, query: str, limit: int = 520) -> str:
    if len(text) <= limit:
        return text
    words = {word[:5] for word in normalized_words(query).split() if len(word) >= 4}
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    ranked = sorted(range(len(sentences)), key=lambda i: (
        -len(words & {word[:5] for word in normalized_words(sentences[i]).split()}), i,
    ))
    picked = {0: sentences[0][:min(limit, 120)]}
    remaining = limit - len(picked[0])
    for i in ranked:
        if remaining <= 1:
            continue
        if i == 0:
            more = sentences[0][len(picked[0]):][:remaining]
            picked[0] += more
            remaining -= len(more)
        else:
            piece = sentences[i]
            if len(piece) >= remaining and i != ranked[0]:
                continue
            picked[i] = piece[:remaining - 1]
            remaining -= len(picked[i]) + 1
    return " ".join(picked[i] for i in sorted(picked))


def business_row(row: dict, *, evidence: bool = False) -> dict:
    identity = {key: row[key] for key in ("resource", "id") if key in row}
    record = row.get("record", {})
    if row.get("resource") != "organisations":
        return {**identity, "record": {"package": record} if evidence else record}
    if evidence:
        fields = ("business_name", "municipality", "types", "offerings", "street_address")
    else:
        fields = _BUSINESS_FIELDS
    facts = {key: record[key] for key in fields if record.get(key) is not None and record[key] != ""}
    description = record.get("description_bs") or record.get("description_en")
    if isinstance(description, str) and description:
        # Keep the complete local description for matching products, aliases and constraints.
        facts["description"] = description
    return {**identity, "record": facts}
