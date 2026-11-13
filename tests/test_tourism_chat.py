import json
import os
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main


class TourismChatTests(unittest.TestCase):
    def setUp(self):
        main._sessions.clear()
        self.client = TestClient(main.app, base_url="https://localhost")

    def call_chat(self, path, query, calls, answer, has_match=None, unmatched_ids=()):
        provider = MagicMock()
        provider.responses.create.side_effect = [
            SimpleNamespace(output=calls, output_text=""),
            *([] if has_match is None else [
                SimpleNamespace(output_text=json.dumps({
                    "has_match": has_match, "unmatched_workbook_ids": list(unmatched_ids),
                })),
            ]),
            SimpleNamespace(output_text=answer),
        ]
        stream = provider.responses.stream.return_value.__enter__.return_value
        stream.__iter__.return_value = iter([
            SimpleNamespace(type="response.output_text.delta", delta=answer),
        ])
        stream.get_final_response.return_value = SimpleNamespace(output_text=answer)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch("main.OpenAI", return_value=provider), \
             patch("main.get_assistant_instructions", return_value=main.TOURISM_INSTRUCTIONS), \
             patch("main.fetch_tourism_catalogue") as live_csv, \
             patch("main.record_chat_turn") as writer:
            response = self.client.post(path, json={"query": query})

        self.assertEqual(response.status_code, 200)
        live_csv.assert_not_called()
        writer.assert_called_once()
        self.assertEqual(writer.call_args.kwargs["assistant_text"], answer)
        self.assertIn(main.TOURISM_TOOL, provider.responses.create.call_args_list[0].kwargs["tools"])
        if path.endswith("/stream"):
            chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            result = chunks[-1]
            self.assertTrue(result["done"])
            self.assertEqual(result["content"], answer)
            synthesis = provider.responses.stream.call_args.kwargs
        else:
            result = response.json()
            self.assertEqual(result["response"], answer)
            synthesis = provider.responses.create.call_args_list[-1].kwargs

        expected_create_calls = 1 + (has_match is not None) + (not path.endswith("/stream"))
        self.assertEqual(provider.responses.create.call_count, expected_create_calls)
        if has_match is not None:
            relevance = provider.responses.create.call_args_list[1].kwargs
            self.assertEqual(relevance["text"]["format"]["name"], "agrotour_relevance")
            self.assertIn({"role": "user", "content": query}, relevance["input"])
            self.assertTrue(json.loads(relevance["input"][-1]["content"])["agrotour_records"])

        self.assertEqual(synthesis["tool_choice"], "none")
        outputs = {
            item["call_id"]: item["output"] for item in synthesis["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        }
        self.assertEqual(outputs, {
            run["call_id"]: run["output"] for run in writer.call_args.kwargs["tool_runs"]
        })
        return result, {call_id: json.loads(output) for call_id, output in outputs.items()}

    def test_catalogue_covers_workbook_pois_routes_business_memberships_and_organizations(self):
        result = main.get_tourism_catalogue("Crkva Svete Trojice na Gorici")
        self.assertEqual(result["source"], "workbook")
        self.assertEqual(len(result["places"]), 281)
        self.assertEqual(len(result["routes"]), 5)
        self.assertEqual(len(result["organizations"]), 18)

        places = {p["name"]: p for p in result["places"]}
        gorica = places["Crkva Svete Trojice na Gorici"]
        self.assertEqual(gorica["municipality"], "Berkovići")
        self.assertIn("Kultura", gorica["categories"])
        self.assertNotIn("description", gorica)
        self.assertNotIn("coordinates", gorica)
        self.assertNotIn("Pećina Provalija", places)

        routes = {r["id"]: r for r in result["routes"]}
        self.assertIn(gorica["id"], routes["route_1699"]["poi_ids"])
        business = next(b for b in result["route_businesses"] if b["name"] == "Restoran Drenovik")
        self.assertEqual(business["municipality"], "Nevesinje")
        self.assertIn(business["id"], routes["route_1590"]["business_ids"])
        self.assertTrue(set(routes["route_1590"]["poi_ids"]).isdisjoint(routes["route_1590"]["business_ids"]))

        organization = next(o for o in result["organizations"] if "Alat" in o["name"])
        self.assertIn("radionice", organization["visitor_options"])
        self.assertTrue({"email", "phone", "contact_person", "internal_note"}.isdisjoint(organization))
        details = {p["id"]: p for p in result["place_details"]}
        self.assertLessEqual(len(details), 8)
        self.assertIn("Hatelji", details[gorica["id"]]["description"])

    def test_catalogue_load_failure_is_reported_without_retrying_live_csv(self):
        with patch("tourism_workbook.load_workbook_catalogue", side_effect=FileNotFoundError), \
             patch("main.fetch_tourism_catalogue") as live_csv:
            result = main.get_tourism_catalogue("Tvrđava Strač")
        live_csv.assert_not_called()
        self.assertTrue(result["error"])
        self.assertEqual(result["places"], [])
        self.assertEqual(result["routes"], [])

    def test_both_chat_routes_use_workbook_focus_preserve_cards_and_coexist_with_knowledge_search(self):
        query = "A kojoj ruti pripada?"
        answer = "Tvrđava Strač pripada ruti Priče uklesane u kamenu."
        calls = [
            SimpleNamespace(type="function_call", name="get_tourism_catalogue", call_id="catalogue",
                            arguments=json.dumps({"focus": "Тврђава Страч"})),
            SimpleNamespace(type="function_call", name="search_knowledge", call_id="knowledge", arguments="{}"),
        ]
        for path in ("/api/chat", "/api/chat/stream"):
            with self.subTest(path=path), patch("main.search_knowledge", return_value={"results": []}) as knowledge:
                result, outputs = self.call_chat(path, query, calls, answer)

                knowledge.assert_called_once_with(query)
                catalogue = outputs["catalogue"]
                self.assertEqual(catalogue["source"], "workbook")
                self.assertIn("Tvrđava Strač", {p["name"] for p in catalogue["place_details"]})
                self.assertEqual([c["id"] for c in result["recommendations"]], ["poi_30", "route_1699"])

    def test_workbook_business_fallback_is_available_on_agrotour_failure_or_empty_results(self):
        query = "Reci mi nešto o restoranu Restoran Drenovik u Nevesinju."
        answer = "U katalogu je Restoran Drenovik u Nevesinju."
        calls = [SimpleNamespace(type="function_call", name="search_agrotour", call_id="agrotour",
                                 arguments='{"prompt":"model rewrite"}')]
        for path, upstream in (
            ("/api/chat", {"data": [], "meta": {"returned": 0}}),
            ("/api/chat/stream", {"error": "AgroTour API is temporarily unavailable.", "data": []}),
        ):
            with self.subTest(path=path), patch("main.search_agrotour", return_value=upstream) as search:
                _, outputs = self.call_chat(path, query, calls, answer)

                search.assert_called_once_with(query)
                result = outputs["agrotour"]
                fallback = result.pop("workbook_fallback")
                self.assertEqual(result, upstream)
                self.assertEqual(fallback["source"], "workbook")
                business = next(b for b in fallback["businesses"] if b["name"] == "Restoran Drenovik")
                self.assertEqual(business["municipality"], "Nevesinje")
                self.assertEqual(business["type"], "Restoran")
                self.assertNotIn("description", business)
                details = {b["id"]: b for b in fallback["business_details"]}
                self.assertTrue(details[business["id"]]["description"])
                self.assertIn("kajmak", details["business_26"]["description"].casefold())
                self.assertIn("kajmak", details["business_28"]["description"].casefold())

    def test_nonempty_agrotour_payload_keeps_live_rows_and_offers_workbook_for_no_match(self):
        query = "Gdje ručati u Nevesinju?"
        answer = "U katalogu je Restoran Drenovik u Nevesinju."
        calls = [SimpleNamespace(type="function_call", name="search_agrotour", call_id="agrotour", arguments="{}")]
        upstream = {"data": [{"id": "3", "resource": "organisations", "score": 0.8,
                              "record": {"business_name": "Smještaj u Trebinju",
                                         "website": "https://example.test/missing"}}],
                    "meta": {"returned": 1, "has_more": False}}
        for path in ("/api/chat", "/api/chat/stream"):
            started = Event()
            original_relevance = main._agrotour_relevance

            def check_link(url):
                started.set()
                return True

            def check_relevance(*args):
                self.assertTrue(started.wait(timeout=5))
                return original_relevance(*args)

            with self.subTest(path=path), patch("main.search_agrotour", return_value=upstream), \
                 patch("main._agrotour_relevance", side_effect=check_relevance), \
                 patch("link_validation._returns_404", side_effect=check_link) as link_check:
                _, outputs = self.call_chat(path, query, calls, answer, has_match=False)
            result = outputs["agrotour"]
            fallback = result.pop("workbook_fallback")
            link_check.assert_called_once_with("https://example.test/missing")
            self.assertEqual(result["meta"], upstream["meta"])
            self.assertEqual(result["data"][0]["record"], {"business_name": "Smještaj u Trebinju"})
            self.assertEqual(fallback["source"], "workbook")
            self.assertIn("Restoran Drenovik", {b["name"] for b in fallback["business_details"]})

    def test_matching_agrotour_results_are_sent_without_workbook_details(self):
        query = "Gdje ručati u Nevesinju?"
        calls = [SimpleNamespace(type="function_call", name="search_agrotour", call_id="agrotour", arguments="{}")]
        upstream = {"data": [{"id": "3", "resource": "organisations",
                              "record": {"business_name": "Drenovik", "municipality": "Nevesinje"}}]}
        with patch("main.search_agrotour", return_value=upstream), patch("main.workbook_business_fallback") as fallback:
            _, outputs = self.call_chat("/api/chat/stream", query, calls, "Restoran Drenovik.", has_match=True)
        fallback.assert_not_called()
        self.assertEqual(outputs["agrotour"], upstream)

    def test_partial_agrotour_match_only_adds_requested_missing_workbook_business(self):
        query = "Šta služi Varduša u Ljubinju i koje sireve pravi Bajčetić kod Trebinja?"
        calls = [SimpleNamespace(type="function_call", name="search_agrotour", call_id="agrotour", arguments="{}")]
        live = {"data": [{"id": "10", "resource": "organisations",
                          "record": {"business_name": "Restoran Varduša", "municipality": "Ljubinje"}}]}
        with patch("main.search_agrotour", return_value=live):
            _, outputs = self.call_chat(
                "/api/chat/stream", query, calls, "Varduša služi jagnjetinu, a Bajčetić pravi škripavac.",
                has_match=True, unmatched_ids=["business_41", "unknown"],
            )
        fallback = outputs["agrotour"].pop("workbook_fallback")
        self.assertEqual(outputs["agrotour"], live)
        self.assertEqual([b["id"] for b in fallback["businesses"]], ["business_41"])
        self.assertIn("škripavca", fallback["business_details"][0]["description"])

    def test_named_business_keeps_workbook_record_when_relevance_check_misses_it(self):
        query = "Koje sireve pravi Gazdinstvo Bajčetić u selu Ljubovo kod Trebinja?"
        calls = [SimpleNamespace(type="function_call", name="search_agrotour", call_id="agrotour", arguments="{}")]
        live = {"data": [{"id": "10", "resource": "organisations",
                          "record": {"business_name": "Restoran Varduša", "municipality": "Ljubinje"}}]}
        with patch("main.search_agrotour", return_value=live):
            _, outputs = self.call_chat(
                "/api/chat/stream", query, calls, "Bajčetić pravi škripavac.", has_match=True,
            )
        fallback = outputs["agrotour"].pop("workbook_fallback")
        self.assertEqual(outputs["agrotour"], live)
        self.assertEqual([b["id"] for b in fallback["businesses"]], ["business_41"])
        self.assertIn("Ljubovo", fallback["business_details"][0]["description"])
        self.assertIn("škripavca", fallback["business_details"][0]["description"])

    def test_named_poi_reaches_model_even_without_a_catalogue_tool_call(self):
        query = "Šta je posebno kod Tvrđave Strač kod Trebinja?"
        provider = MagicMock()
        provider.responses.create.return_value = SimpleNamespace(output=[], output_text="Austrougarsko utvrđenje.")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch("main.OpenAI", return_value=provider), \
             patch("main.get_assistant_instructions", return_value=main.TOURISM_INSTRUCTIONS):
            self.assertEqual(main.answer_query(query), "Austrougarsko utvrđenje.")
        items = provider.responses.create.call_args.kwargs["input"]
        self.assertEqual(items[-1]["content"], query)
        self.assertIn("ukopan", items[-2]["content"])
        self.assertIn("Hercegovina za cijelu porodicu", items[-2]["content"])
        self.assertIn("Priče uklesane u kamenu", items[-2]["content"])
        catalogue = main.get_tourism_catalogue("A kojoj ruti pripada?", "Tvrđava Strač")
        place = next(item for item in catalogue["place_details"] if item["id"] == "poi_30")
        self.assertEqual(place["route_names"], ["Priče uklesane u kamenu", "Hercegovina za cijelu porodicu"])


if __name__ == "__main__":
    unittest.main()
