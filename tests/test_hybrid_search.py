import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hybrid_search


class HybridSearchTests(unittest.TestCase):
    def tearDown(self) -> None:
        hybrid_search._load_encoder.cache_clear()

    def test_missing_stats_returns_configuration_error(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-openai",
                "PINECONE_API_KEY": "test-pinecone",
                "BM25_STATS_PATH": "",
                "BM25_STATS_URL": "",
            },
        ):
            result = hybrid_search.search_knowledge("Trebinje")

        self.assertEqual(result["results"], [])
        self.assertIn("not configured", result["error"])

    def test_hybrid_query_uses_expected_index_namespace_and_weights(self) -> None:
        stats = {
            "vocab": {"Trebinje": 17},
            "df": {"Trebinje": 2},
            "N": 10,
            "avgdl": 12.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            stats_path = Path(directory) / "bm25.json"
            stats_path.write_text(json.dumps(stats), encoding="utf-8")

            embeddings = Mock()
            embeddings.create.return_value = SimpleNamespace(
                data=[SimpleNamespace(embedding=[3.0, 4.0])]
            )
            openai_client = Mock(embeddings=embeddings)

            index = Mock()
            index.query.return_value = SimpleNamespace(
                matches=[
                    SimpleNamespace(
                        score=0.81234,
                        metadata={
                            "text": "Stari grad je u Trebinju.",
                            "source": "Trebinje.docx",
                            "date": 20260826,
                            "id": "internal-id",
                        },
                    )
                ]
            )
            pinecone_client = Mock()
            pinecone_client.Index.return_value = index

            with (
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "test-openai",
                        "PINECONE_API_KEY": "test-pinecone",
                        "BM25_STATS_PATH": str(stats_path),
                        "BM25_STATS_URL": "",
                    },
                ),
                patch("hybrid_search.OpenAI", return_value=openai_client),
                patch("hybrid_search.Pinecone", return_value=pinecone_client),
            ):
                result = hybrid_search.search_knowledge("Trebinje")

        pinecone_client.Index.assert_called_once_with("neo-positive")
        query = index.query.call_args.kwargs
        self.assertEqual(query["namespace"], "smartherz")
        self.assertEqual(query["top_k"], 5)
        self.assertEqual(query["vector"], [0.3, 0.4])
        self.assertEqual(query["sparse_vector"]["indices"], [17])
        self.assertAlmostEqual(query["sparse_vector"]["values"][0], 0.5)
        self.assertEqual(
            result,
            {
                "results": [
                    {
                        "text": "Stari grad je u Trebinju.",
                        "source": "Trebinje.docx",
                        "date": 20260826,
                        "score": 0.8123,
                    }
                ]
            },
        )
        self.assertNotIn("id", result["results"][0])

    def test_encoder_uses_corpus_document_frequency(self) -> None:
        encoder = hybrid_search.BM25QueryEncoder(
            {
                "vocab": {"vino": 3},
                "df": {"vino": 4},
                "N": 10,
                "avgdl": 8.0,
            }
        )

        encoded = encoder.encode("vino vino nepoznato")

        self.assertEqual(encoded["indices"], [3])
        self.assertAlmostEqual(
            encoded["values"][0],
            math.log((10 - 4 + 0.5) / (4 + 0.5) + 1),
        )


if __name__ == "__main__":
    unittest.main()
