import json
import logging
import math
import os
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen

from openai import OpenAI
from pinecone import Pinecone

PINECONE_INDEX = "neo-positive"
PINECONE_NAMESPACE = "smartherz"
EMBEDDING_MODEL = "text-embedding-3-large"
HYBRID_ALPHA = 0.5
HYBRID_TOP_K = 5

logger = logging.getLogger(__name__)


class BM25QueryEncoder:
    def __init__(self, stats: dict) -> None:
        self.vocab = stats["vocab"]
        self.document_frequency = stats["df"]
        self.document_count = stats["N"]

    def encode(self, text: str) -> dict[str, list]:
        indices: list[int] = []
        values: list[float] = []
        for term in set(text.split()):
            if term not in self.vocab:
                continue
            term_frequency = self.document_frequency.get(term, 0)
            inverse_frequency = math.log(
                (self.document_count - term_frequency + 0.5)
                / (term_frequency + 0.5)
                + 1
            )
            indices.append(self.vocab[term])
            values.append(inverse_frequency)
        return {"indices": indices, "values": values}


def _normalize(values: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in values))
    return [value / magnitude for value in values] if magnitude else list(values)


@lru_cache(maxsize=4)
def _load_encoder(location: str) -> BM25QueryEncoder:
    if location.startswith(("http://", "https://")):
        with urlopen(location, timeout=10) as response:
            stats = json.load(response)
    else:
        with Path(location).open(encoding="utf-8") as stats_file:
            stats = json.load(stats_file)
    return BM25QueryEncoder(stats)


def _stats_location() -> str | None:
    return os.getenv("BM25_STATS_PATH") or os.getenv("BM25_STATS_URL")


def search_knowledge(prompt: str) -> dict:
    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    stats_location = _stats_location()
    if not pinecone_api_key or not openai_api_key or not stats_location:
        return {
            "error": "Hybrid knowledge search is not configured.",
            "results": [],
        }

    try:
        encoder = _load_encoder(stats_location)
        embedding = OpenAI(api_key=openai_api_key, timeout=30.0).embeddings.create(
            model=EMBEDDING_MODEL,
            input=[prompt.replace("\n", " ")[:8_000]],
        ).data[0].embedding
        sparse = encoder.encode(prompt)
        query = {
            "namespace": PINECONE_NAMESPACE,
            "top_k": HYBRID_TOP_K,
            "vector": [value * HYBRID_ALPHA for value in _normalize(embedding)],
            "include_metadata": True,
        }
        if sparse["indices"]:
            query["sparse_vector"] = {
                "indices": sparse["indices"],
                "values": [
                    value * (1 - HYBRID_ALPHA)
                    for value in _normalize(sparse["values"])
                ],
            }

        matches = Pinecone(api_key=pinecone_api_key).Index(PINECONE_INDEX).query(
            **query
        ).matches
        results = []
        for match in matches:
            metadata = match.metadata or {}
            results.append(
                {
                    key: value
                    for key, value in {
                        "text": metadata.get("text"),
                        "source": metadata.get("source"),
                        "date": metadata.get("date"),
                        "score": round(float(match.score), 4),
                    }.items()
                    if value is not None
                }
            )
        return {"results": results}
    except Exception as exc:  # The model receives no infrastructure details.
        logger.warning("Hybrid knowledge search failed error_type=%s", type(exc).__name__)
        return {
            "error": "Hybrid knowledge search is temporarily unavailable.",
            "results": [],
        }
