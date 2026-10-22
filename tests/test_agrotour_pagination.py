import io
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import main


def page(offset, count, has_more):
    return {
        "query": "original query",
        "data": [{"resource": "organisations", "id": str(i), "record": {"business_name": f"Producer {i}"}}
                 for i in range(offset, offset + count)],
        "meta": {"resource": "all", "limit": 50, "offset": offset, "returned": count, "has_more": has_more},
    }


class AgroTourPaginationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"AGROTOUR_API_KEY": "test-agrotour-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_fetches_all_pages_without_sending_a_result_limit(self):
        pages = [page(0, 50, True), page(50, 50, True), page(100, 23, False)]
        prompt = "Izlistaj mi sve proizvođače gatačkog kajmaka."
        with patch("main.urlopen", side_effect=[io.BytesIO(json.dumps(p).encode()) for p in pages]) as source:
            result = main.search_agrotour(prompt)
        self.assertEqual([row["id"] for row in result["data"]], list(map(str, range(123))))
        self.assertEqual(result["meta"], {"resource": "all", "offset": 0, "returned": 123, "has_more": False})
        for call, offset in zip(source.call_args_list, [0, 50, 100], strict=True):
            request = call.args[0]
            self.assertEqual(parse_qs(urlsplit(request.full_url).query),
                             {"q": [prompt], "resource": ["all"], "offset": [str(offset)]})
            self.assertEqual(request.get_header("X-api-key"), "test-agrotour-key")

    def test_empty_complete_result_needs_only_one_request(self):
        with patch("main.urlopen", return_value=io.BytesIO(json.dumps(page(0, 0, False)).encode())) as source:
            result = main.search_agrotour("No match")
        self.assertEqual(result["data"], [])
        self.assertEqual(result["meta"]["returned"], 0)
        source.assert_called_once()

    def test_later_page_failure_does_not_return_a_partial_list(self):
        error = HTTPError("https://example.test", 503, "Unavailable", {}, None)
        with patch("main.urlopen", side_effect=[io.BytesIO(json.dumps(page(0, 50, True)).encode()), error]):
            result = main.search_agrotour("All producers")
        self.assertEqual(result["data"], [])
        self.assertIn("503", result["error"])

    def test_invalid_pagination_stops_without_an_infinite_loop(self):
        repeated = page(0, 50, True)
        repeated["meta"]["offset"] = 50
        for bad_page in [page(50, 0, True), page(0, 50, True), repeated]:
            with self.subTest(bad_page=bad_page["meta"]):
                with patch("main.urlopen", side_effect=[io.BytesIO(json.dumps(page(0, 50, True)).encode()),
                                                        io.BytesIO(json.dumps(bad_page).encode())]) as source:
                    result = main.search_agrotour("All producers")
                self.assertEqual(source.call_count, 2)
                self.assertEqual(result["data"], [])
                self.assertIn("error", result)

    def test_malformed_page_is_not_reported_as_an_empty_success(self):
        with patch("main.urlopen", return_value=io.BytesIO(b'{"data": []}')):
            result = main.search_agrotour("All producers")
        self.assertEqual(result["data"], [])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
