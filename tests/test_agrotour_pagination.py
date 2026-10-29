import io
import json
import os
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

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
            self.assertEqual(call.kwargs["timeout"], 20)

    def test_first_page_timeout_retries_once_with_the_same_request(self):
        for error in (TimeoutError("read timed out"), URLError(TimeoutError("read timed out"))):
            with self.subTest(error=type(error).__name__), \
                 patch("main.urlopen", side_effect=[error, io.BytesIO(json.dumps(page(0, 2, False)).encode())]) as source:
                result = main.search_agrotour("Lunch in Trebinje")
            self.assertEqual([row["id"] for row in result["data"]], ["0", "1"])
            self.assertEqual(source.call_count, 2)
            self.assertIs(source.call_args_list[0].args[0], source.call_args_list[1].args[0])
            self.assertTrue(all(call.kwargs["timeout"] == 20 for call in source.call_args_list))

    def test_later_page_timeout_retries_that_page_without_duplicate_results(self):
        with patch("main.urlopen", side_effect=[
            io.BytesIO(json.dumps(page(0, 50, True)).encode()),
            TimeoutError("read timed out"),
            io.BytesIO(json.dumps(page(50, 17, False)).encode()),
        ]) as source:
            result = main.search_agrotour("All producers")
        self.assertEqual([row["id"] for row in result["data"]], list(map(str, range(67))))
        self.assertEqual(result["meta"]["returned"], 67)
        self.assertFalse(result["meta"]["has_more"])
        offsets = [parse_qs(urlsplit(call.args[0].full_url).query)["offset"]
                   for call in source.call_args_list]
        self.assertEqual(offsets, [["0"], ["50"], ["50"]])

    def test_timeout_while_reading_response_body_also_retries(self):
        stalled_response = MagicMock()
        stalled_response.__enter__.return_value.read.side_effect = TimeoutError("body timed out")
        with patch("main.urlopen", side_effect=[
            stalled_response, io.BytesIO(json.dumps(page(0, 1, False)).encode()),
        ]) as source:
            result = main.search_agrotour("Lunch")
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(source.call_count, 2)
        stalled_response.__exit__.assert_called_once()

    def test_second_timeout_stops_and_does_not_return_a_partial_list(self):
        with patch("main.urlopen", side_effect=[
            io.BytesIO(json.dumps(page(0, 50, True)).encode()),
            TimeoutError("first timeout"), URLError(TimeoutError("second timeout")),
        ]) as source:
            result = main.search_agrotour("All producers")
        self.assertEqual(source.call_count, 3)
        self.assertEqual(result, {"error": "AgroTour API is temporarily unavailable.", "data": []})

    def test_non_timeout_failures_are_not_retried(self):
        for error in (
            HTTPError("https://example.test", 401, "Unauthorized", {}, None),
            HTTPError("https://example.test", 503, "Unavailable", {}, None),
            URLError("DNS failure"),
        ):
            with self.subTest(error=type(error).__name__), patch("main.urlopen", side_effect=error) as source:
                result = main.search_agrotour("Lunch")
            source.assert_called_once()
            self.assertEqual(result["data"], [])
            self.assertIn("error", result)

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
