import io
import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import main


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "INTEGRATION_API_KEY": "local-integration-test-key",
            "INTEGRATION_TOURISM_DEMO": "true",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(main.app)
        self.headers = {"X-API-Key": "local-integration-test-key"}
        self.kpis = "/api/v1/integration/kpis?date_from=2026-10-01&date_to=2026-10-07"
        self.sql_context = MagicMock()
        self.connection = self.sql_context.__enter__.return_value
        self.cursor = self.connection.cursor.return_value
        self.cursor.fetchone.return_value = (0, None)

    @staticmethod
    def event(event_id, **overrides):
        return {
            "id": event_id,
            "title": {"rendered": "Događaj &amp; kultura"},
            "content": {"rendered": "<p>Opis <strong>događaja</strong>.</p>"},
            "link": f"https://example.test/events/{event_id}",
            "mec_start_date": "2026-10-15",
            "mec_end_date": "2026-10-16",
            **overrides,
        }

    @staticmethod
    def wp_response(rows, pages=1):
        response = io.BytesIO(json.dumps(rows).encode())
        response.headers = {"X-WP-TotalPages": str(pages)}
        return response

    def test_missing_wrong_and_unicode_keys_reject_both_endpoints_before_sources(self):
        with patch("main._sql_connection") as sql, patch("main.urlopen") as wordpress:
            for path in [self.kpis, "/api/v1/integration/tourism"]:
                for headers in [{}, {"X-API-Key": "wrong"}, {"X-API-Key": "another-key"}]:
                    with self.subTest(path=path, headers=headers):
                        self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
            with self.assertRaises(main.HTTPException) as raised:
                main._require_integration_key("ključ")
            self.assertEqual(raised.exception.status_code, 401)
            sql.assert_not_called()
            wordpress.assert_not_called()

    def test_unconfigured_key_returns_503(self):
        with patch.dict(os.environ, {"INTEGRATION_API_KEY": ""}), patch("main._sql_connection") as sql:
            for path in [self.kpis, "/api/v1/integration/tourism"]:
                self.assertEqual(self.client.get(path, headers=self.headers).status_code, 503)
            sql.assert_not_called()

    def test_kpis_use_inclusive_utc_bounds_and_keep_missing_metrics_null(self):
        with patch("main._sql_connection", return_value=self.sql_context):
            response = self.client.get(self.kpis, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "date_from": "2026-10-01", "date_to": "2026-10-07",
            "ai_conversations": 0, "recommendations_generated": None,
            "average_session_duration_seconds": None,
        })
        args = self.cursor.execute.call_args.args
        self.assertEqual(args[1:], (main.APP_ID, datetime(2026, 10, 1), datetime(2026, 10, 8)))
        self.assertIn("COUNT(DISTINCT thread_id)", args[0])
        self.assertIn("closed_at >= created_at", args[0])

    def test_single_leap_day_and_year_end_bounds(self):
        for start, end, exclusive_end in [
            ("2024-02-29", "2024-02-29", datetime(2024, 3, 1)),
            ("2026-12-31", "2026-12-31", datetime(2027, 1, 1)),
        ]:
            with self.subTest(start=start), patch("main._sql_connection", return_value=self.sql_context):
                response = self.client.get("/api/v1/integration/kpis", headers=self.headers,
                                           params={"date_from": start, "date_to": end})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.cursor.execute.call_args.args[-1], exclusive_end)

    def test_available_duration_is_returned_without_fabricating_recommendations(self):
        self.cursor.fetchone.return_value = (4, 125.25)
        with patch("main._sql_connection", return_value=self.sql_context):
            response = self.client.get(self.kpis, headers=self.headers)
        self.assertEqual(response.json()["ai_conversations"], 4)
        self.assertEqual(response.json()["average_session_duration_seconds"], 125.25)
        self.assertIsNone(response.json()["recommendations_generated"])

    def test_invalid_missing_reversed_and_out_of_range_dates_do_not_query_sql(self):
        cases = [
            {}, {"date_from": "2026-10-01"},
            {"date_from": "2026-10-08", "date_to": "2026-10-07"},
            {"date_from": "2026-02-30", "date_to": "2026-03-01"},
            {"date_from": "2026-1-01", "date_to": "2026-10-07"},
            {"date_from": "1680000000", "date_to": "2026-10-07"},
            {"date_from": "2026-10-01T00:00:00Z", "date_to": "2026-10-07"},
            {"date_from": "9999-12-31", "date_to": "9999-12-31"},
        ]
        with patch("main._sql_connection") as sql:
            for params in cases:
                with self.subTest(params=params):
                    response = self.client.get("/api/v1/integration/kpis", headers=self.headers, params=params)
                    self.assertEqual(response.status_code, 422)
            sql.assert_not_called()

    def test_sql_failure_returns_503_without_connection_details(self):
        with patch("main._sql_connection", side_effect=RuntimeError("private password and host")):
            response = self.client.get(self.kpis, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    def test_tourism_includes_demo_catalogue_and_all_event_pages(self):
        responses = [self.wp_response([self.event(3), self.event(4)], pages=2),
                     self.wp_response([self.event(5)], pages=2)]
        with patch("main.urlopen", side_effect=responses) as wordpress:
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["places"]), 10)
        self.assertEqual(len(body["routes"]), 1)
        self.assertTrue(all(item["id"].startswith("demo_") for item in body["places"] + body["routes"]))
        self.assertEqual([event["id"] for event in body["events"]], ["event_3", "event_4", "event_5"])
        self.assertEqual(body["events"][0]["name"], "Događaj & kultura")
        self.assertEqual(body["events"][0]["description"], "Opis događaja.")
        self.assertEqual(body["events"][0]["start_date"], "2026-10-15")
        self.assertIsNotNone(datetime.fromisoformat(body["generated_at"]).utcoffset())
        self.assertEqual(response.headers["X-SmartHerz-Data-Mode"], "demo-catalogue-live-events")
        requested_pages = [parse_qs(urlsplit(call.args[0].full_url).query)["page"] for call in wordpress.call_args_list]
        self.assertEqual(requested_pages, [["1"], ["2"]])

    def test_demo_ids_are_stable_and_absent_tourism_fields_are_omitted(self):
        with patch("main.urlopen", side_effect=[self.wp_response([]), self.wp_response([])]):
            first = self.client.get("/api/v1/integration/tourism", headers=self.headers).json()
            second = self.client.get("/api/v1/integration/tourism", headers=self.headers).json()
        self.assertEqual(first["places"], second["places"])
        self.assertEqual(first["routes"], second["routes"])
        self.assertNotIn("description", first["places"][1])
        self.assertNotIn("url", first["places"][0])

    def test_zero_published_events_is_a_valid_empty_collection(self):
        with patch("main.urlopen", return_value=self.wp_response([], pages=0)):
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])

    def test_demo_must_be_explicitly_enabled(self):
        for setting in ["", "false"]:
            with patch.dict(os.environ, {"INTEGRATION_TOURISM_DEMO": setting}), patch("main.urlopen") as wordpress:
                response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
                self.assertEqual(response.status_code, 503)
                wordpress.assert_not_called()

    def test_wordpress_failure_never_returns_a_partial_success(self):
        for failure in [URLError("unavailable"), self.wp_response({"error": "not a collection"}),
                        self.wp_response([self.event(3, mec_start_date="bad-date")])]:
            with self.subTest(failure=type(failure).__name__):
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch("main.urlopen", **kwargs):
                    response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
                self.assertEqual(response.status_code, 503)
        with patch("main.urlopen", side_effect=[self.wp_response([self.event(3)], pages=2), URLError("page 2 failed")]):
            self.assertEqual(self.client.get("/api/v1/integration/tourism", headers=self.headers).status_code, 503)
        missing_pagination = self.wp_response([self.event(3)])
        missing_pagination.headers = {}
        with patch("main.urlopen", return_value=missing_pagination):
            self.assertEqual(self.client.get("/api/v1/integration/tourism", headers=self.headers).status_code, 503)

    def test_openapi_exposes_api_key_security_and_responses(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["components"]["securitySchemes"]["IntegrationApiKey"], {
            "type": "apiKey", "in": "header", "name": "X-API-Key",
        })
        for path in ["/api/v1/integration/kpis", "/api/v1/integration/tourism"]:
            operation = schema["paths"][path]["get"]
            self.assertEqual(operation["security"], [{"IntegrationApiKey": []}])
            self.assertIn("401", operation["responses"])
            self.assertIn("503", operation["responses"])


if __name__ == "__main__":
    unittest.main()
