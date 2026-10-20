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
import integration_key_links as links


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        auth_sql = patch.object(links, 'sql_connection')
        self.auth_sql = auth_sql.start()
        self.addCleanup(auth_sql.stop)
        self.auth_cursor = self.auth_sql.return_value.__enter__.return_value.cursor.return_value
        self.auth_cursor.execute.return_value.fetchone.return_value = (1,)
        self.client = TestClient(main.app)
        self.headers = {"X-API-Key": "smh_" + "a" * 43}
        self.catalogue = json.loads((main.BASE_DIR / "tests" / "fixtures" / "tourism_20261020.json").read_text(encoding="utf-8"))
        self.catalogue_patch = patch("main.fetch_tourism_catalogue", return_value=self.catalogue)
        self.catalogue_mock = self.catalogue_patch.start()
        self.addCleanup(self.catalogue_patch.stop)
        self.destinations = [main.IntegrationPlace(
            id=f"destination_{page_id}", name=f"Destination {page_id}", category="destination",
            slug=f"destination-{page_id}", url=f"https://example.test/destination/{page_id}",
        ) for page_id in main._INTEGRATION_DESTINATION_PAGE_IDS]
        self.routes = [main.IntegrationRoute(
            id=f"route_{page_id}", name=f"Route {page_id}", url=f"https://example.test/route/{page_id}",
        ) for page_id in main._INTEGRATION_ROUTE_PAGE_IDS]
        self.pages_patch = patch("main._fetch_integration_pages", return_value=(self.destinations, self.routes))
        self.pages_mock = self.pages_patch.start()
        self.addCleanup(self.pages_patch.stop)
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
    def wp_response(rows, pages=1, total=None):
        response = io.BytesIO(json.dumps(rows).encode())
        response.headers = {"X-WP-TotalPages": str(pages), "X-WP-Total": str(len(rows) if total is None else total)}
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
            self.pages_mock.assert_not_called()

    def test_auth_database_failure_returns_503_before_sources(self):
        with patch.object(links, 'sql_connection', side_effect=RuntimeError('private database')), patch("main._sql_connection") as sql:
            for path in [self.kpis, "/api/v1/integration/tourism"]:
                self.assertEqual(self.client.get(path, headers=self.headers).status_code, 503)
            sql.assert_not_called()

    def test_unknown_or_revoked_partner_key_rejects_both_endpoints(self):
        self.auth_cursor.execute.return_value.fetchone.return_value = None
        with patch("main._sql_connection") as sql:
            for path in [self.kpis, "/api/v1/integration/tourism"]:
                self.assertEqual(self.client.get(path, headers=self.headers).status_code, 401)
            sql.assert_not_called()
        self.pages_mock.assert_not_called()

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

    def test_tourism_includes_real_pois_and_all_event_pages(self):
        responses = [self.wp_response([self.event(3), self.event(4)], pages=2, total=3),
                     self.wp_response([self.event(5)], pages=2, total=3)]
        with patch("main.urlopen", side_effect=responses) as wordpress:
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["places"]), 289)
        self.assertEqual(len(body["routes"]), 5)
        pois = [item for item in body["places"] if item["category"] == "poi"]
        self.assertEqual(len(pois), 281)
        self.assertTrue(all(item["id"].startswith("poi_") and item["municipality"] for item in pois))
        self.assertTrue(all("description" not in item and "latitude" not in item and "longitude" not in item for item in pois))
        route_poi_ids = [row["poi_ids"] for row in body["routes"]]
        self.assertEqual([len(members) for members in route_poi_ids], [6, 179, 57, 174, 65])
        self.assertTrue(all(len(members) == len(set(members)) for members in route_poi_ids))
        self.assertEqual(set().union(*map(set, route_poi_ids)), {row["id"] for row in pois})
        self.assertFalse(any(item["id"].startswith("demo_") for item in body["places"]))
        self.assertTrue(all(item["id"].startswith("route_") for item in body["routes"]))
        self.assertEqual([event["id"] for event in body["events"]], ["event_3", "event_4", "event_5"])
        self.assertEqual(body["events"][0]["name"], "Događaj & kultura")
        self.assertEqual(body["events"][0]["description"], "Opis događaja.")
        self.assertEqual(body["events"][0]["start_date"], "2026-10-15")
        self.assertNotIn("poi_ids", main.app.openapi()["components"]["schemas"]["IntegrationEvent"]["properties"])
        self.assertIsNotNone(datetime.fromisoformat(body["generated_at"]).utcoffset())
        self.assertEqual(response.headers["X-SmartHerz-Data-Mode"], "live-csv-live-wordpress")
        requested_pages = [parse_qs(urlsplit(call.args[0].full_url).query)["page"] for call in wordpress.call_args_list]
        self.assertEqual(requested_pages, [["1"], ["2"]])

    def test_catalogue_ids_are_stable_and_absent_tourism_fields_are_omitted(self):
        with patch("main.urlopen", side_effect=[self.wp_response([]), self.wp_response([])]):
            first = self.client.get("/api/v1/integration/tourism", headers=self.headers).json()
            second = self.client.get("/api/v1/integration/tourism", headers=self.headers).json()
        self.assertEqual(first["places"], second["places"])
        self.assertEqual(first["routes"], second["routes"])
        self.assertNotIn("description", first["places"][1])
        self.assertNotIn("slug", first["places"][-1])

    def raw_pages(self):
        rows = [{
            "id": int(record.id.split("_", 1)[1]),
            "title": {"rendered": f"<b>{record.name}</b> &amp; portal"},
            "slug": getattr(record, "slug", None) or "route-slug",
            "link": record.url,
            "content": {"rendered": "Divi content should not become a destination description."},
        } for record in self.destinations + self.routes]
        return sorted(rows, key=lambda row: row["id"])

    def test_live_destination_and_route_mapping(self):
        self.pages_patch.stop()
        with patch("main.urlopen", side_effect=[self.wp_response(self.raw_pages()), self.wp_response([])]) as wordpress:
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["places"]), 289)
        self.assertEqual(len(body["routes"]), 5)
        destinations = [row for row in body["places"] if row["category"] == "destination"]
        self.assertEqual({row["id"] for row in destinations}, {row.id for row in self.destinations})
        self.assertEqual({row["id"] for row in body["routes"]}, {row.id for row in self.routes})
        self.assertEqual(destinations[0]["name"], "Destination 347 & portal")
        self.assertEqual(destinations[0]["slug"], "destination-347")
        self.assertEqual(destinations[0]["url"], "https://example.test/destination/347")
        self.assertNotIn("description", destinations[0])
        self.assertTrue(all("url" in row for row in body["routes"]))
        query = parse_qs(urlsplit(wordpress.call_args_list[0].args[0].full_url).query)
        self.assertEqual(query["_fields"], ["id,title,slug,link"])
        self.assertEqual(set(map(int, query["include"][0].split(","))),
                         set(main._INTEGRATION_DESTINATION_PAGE_IDS + main._INTEGRATION_ROUTE_PAGE_IDS))

    def test_missing_required_destination_or_route_never_returns_partial_success(self):
        self.pages_patch.stop()
        for missing_id in [347, 1590]:
            rows = [row for row in self.raw_pages() if row["id"] != missing_id]
            with self.subTest(missing_id=missing_id), patch("main.urlopen", return_value=self.wp_response(rows)) as wordpress:
                response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("places", response.json())
                self.assertEqual(wordpress.call_count, 1)

    def test_pages_source_failure_returns_503(self):
        self.pages_patch.stop()
        with patch("main.urlopen", side_effect=URLError("pages unavailable")):
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 503)

    def test_zero_published_events_is_a_valid_empty_collection(self):
        with patch("main.urlopen", return_value=self.wp_response([], pages=0)):
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [])

    def test_announced_but_missing_events_return_503(self):
        pages = [self.wp_response([self.event(3)], pages=2, total=2),
                 self.wp_response([], pages=2, total=2)]
        with patch("main.urlopen", side_effect=pages):
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("events", response.json())

    def test_duplicate_event_ids_return_503(self):
        pages = [self.wp_response([self.event(3)], pages=2, total=2),
                 self.wp_response([self.event(3)], pages=2, total=2)]
        with patch("main.urlopen", side_effect=pages):
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 503)

    def test_catalogue_is_available_without_demo_configuration(self):
        with patch("main.urlopen", return_value=self.wp_response([])):
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["places"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("poi_1", ids)

    def test_invalid_route_links_and_duplicate_pois_never_return_partial_success(self):
        catalogue = self.catalogue
        broken = []
        for mutate in [
            lambda data: data["route_poi_ids"]["route_1590"].append("poi_unknown"),
            lambda data: data["route_poi_ids"]["route_1590"].append("poi_1"),
            lambda data: data["route_poi_ids"].pop("route_1590"),
            lambda data: data["places"].append(data["places"][0]),
        ]:
            data = json.loads(json.dumps(catalogue))
            mutate(data)
            broken.append(data)
        for data in broken:
            with self.subTest(), patch("main.fetch_tourism_catalogue", return_value=data), patch("main.urlopen") as wordpress:
                response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("places", response.json())
                wordpress.assert_not_called()

    def test_unavailable_csv_never_returns_partial_success_or_snapshot_fallback(self):
        with patch("main.fetch_tourism_catalogue", side_effect=URLError("CSV unavailable")), patch("main.urlopen") as wordpress:
            response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("places", response.json())
        wordpress.assert_not_called()

    def test_next_request_reads_updated_csv_data_without_local_catalogue_file(self):
        updated = json.loads(json.dumps(self.catalogue))
        updated["places"][0]["name"] = "Updated name from CSV"
        with patch("main.fetch_tourism_catalogue", side_effect=[self.catalogue, updated]) as source, \
             patch("main.Path.read_text", side_effect=OSError("no local snapshot")), \
             patch("main.urlopen", side_effect=[self.wp_response([]), self.wp_response([])]):
            first = self.client.get("/api/v1/integration/tourism", headers=self.headers)
            second = self.client.get("/api/v1/integration/tourism", headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(source.call_count, 2)
        self.assertEqual(next(p["name"] for p in second.json()["places"] if p["id"] == "poi_1"), "Updated name from CSV")
        self.assertNotEqual(first.json()["places"], second.json()["places"])

    def test_wordpress_failure_never_returns_a_partial_success(self):
        for failure in [URLError("unavailable"), self.wp_response({"error": "not a collection"}),
                        self.wp_response([self.event(3, mec_start_date="bad-date")])]:
            with self.subTest(failure=type(failure).__name__):
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch("main.urlopen", **kwargs):
                    response = self.client.get("/api/v1/integration/tourism", headers=self.headers)
                self.assertEqual(response.status_code, 503)
        with patch("main.urlopen", side_effect=[self.wp_response([self.event(3)], pages=2, total=2), URLError("page 2 failed")]):
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
