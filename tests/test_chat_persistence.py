import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import chat_persistence
import main


class ChatPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.database = MagicMock()
        self.connection_context = MagicMock()
        self.connection_context.__enter__.return_value = self.database
        self.payload = {
            "thread_id": "smh_test", "app_id": 66, "client_id": 18,
            "query": "Šta obići?'); DROP TABLE chat.Messages; --",
            "assistant_text": "Obiđite Trebinje.",
            "filters": {"destinations": ["trebinje"]},
            "tool_runs": [
                {"name": "search_knowledge", "call_id": "call-1",
                 "arguments": {"prompt": "Šta obići?"},
                 "output": json.dumps({"results": ["Č" * 40_000]}, ensure_ascii=False)},
                {"name": "search_knowledge", "call_id": "call-2",
                 "arguments": {"prompt": "Drugo pitanje"},
                 "output": '{"results": []}'},
            ],
        }

    def test_atomic_turn_preserves_large_outputs_repeated_tools_and_parameterized_text(self):
        with patch("chat_persistence.sql_connection", return_value=self.connection_context):
            self.assertTrue(chat_persistence.record_chat_turn(**self.payload))

        calls = self.database.cursor.return_value.execute.call_args_list
        self.assertEqual(len(calls), 5)
        thread_sql, *thread_values = calls[0].args
        self.assertIn("WHERE thread_id = ? AND app_id = ?", thread_sql)
        self.assertIn("THROW", thread_sql)
        self.assertEqual(thread_values, ["smh_test", 66, "smh_test", "smh_test", 66])
        rows = [call.args[1:] for call in calls[1:]]
        self.assertEqual([row[2] for row in rows], ["user", "tool", "tool", "assistant"])
        self.assertTrue(all(row[:2] == ("smh_test", "Anon") for row in rows))
        self.assertEqual(rows[0][3], self.payload["query"])
        self.assertNotIn(self.payload["query"], calls[1].args[0])
        self.assertEqual(rows[1][3], self.payload["tool_runs"][0]["output"])
        self.assertEqual(rows[2][3], self.payload["tool_runs"][1]["output"])
        self.assertEqual(rows[3][3], self.payload["assistant_text"])
        details = [json.loads(row[4]) for row in rows]
        self.assertEqual(len({item["turn_id"] for item in details}), 1)
        self.assertTrue(all(item["client_id"] == 18 for item in details))
        self.assertEqual(details[0]["filters"], self.payload["filters"])
        for item, run in zip(details[1:3], self.payload["tool_runs"]):
            self.assertEqual(item["tool_names"], run["name"])
            self.assertEqual(item["call_id"], run["call_id"])
            self.assertEqual(item["arguments"], run["arguments"])
        self.database.commit.assert_called_once()
        self.database.rollback.assert_not_called()

    def test_chat_without_tools_has_no_empty_tool_row(self):
        with patch("chat_persistence.sql_connection", return_value=self.connection_context):
            self.assertTrue(chat_persistence.record_chat_turn(**{**self.payload, "tool_runs": []}))
        calls = self.database.cursor.return_value.execute.call_args_list
        self.assertEqual([call.args[3] for call in calls[1:]], ["user", "assistant"])

    def test_failed_insert_rolls_back_all_rows_and_does_not_log_payload_or_error_details(self):
        cursor = self.database.cursor.return_value
        cursor.execute.side_effect = [None, None, RuntimeError("private SQL details")]
        with patch("chat_persistence.sql_connection", return_value=self.connection_context), \
             self.assertLogs("chat_persistence", level="WARNING") as logs:
            self.assertFalse(chat_persistence.record_chat_turn(**self.payload))
        self.database.commit.assert_not_called()
        self.database.rollback.assert_called_once()
        self.assertEqual(len(cursor.execute.call_args_list), 3)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("private SQL details", logs.output[0])
        self.assertNotIn(self.payload["query"], logs.output[0])

    def test_scope_rejection_rolls_back_before_any_message_insert(self):
        self.database.cursor.return_value.execute.side_effect = RuntimeError("wrong app")
        with patch("chat_persistence.sql_connection", return_value=self.connection_context):
            self.assertFalse(chat_persistence.record_chat_turn(**self.payload))
        self.database.cursor.return_value.execute.assert_called_once()
        self.database.commit.assert_not_called()
        self.database.rollback.assert_called_once()

    def test_commit_failure_also_rolls_back(self):
        self.database.commit.side_effect = RuntimeError("commit failed")
        with patch("chat_persistence.sql_connection", return_value=self.connection_context):
            self.assertFalse(chat_persistence.record_chat_turn(**self.payload))
        self.database.rollback.assert_called_once()


class ChatPersistenceRoutingTests(unittest.TestCase):
    def setUp(self):
        main._sessions.clear()
        self.client = TestClient(main.app, base_url="https://localhost")

    def test_both_chat_routes_capture_the_processed_outputs_and_actual_arguments(self):
        names = ["search_agrotour", "search_knowledge", "get_tourism_catalogue",
                 "get_tourism_events", "get_weather"]
        arguments = [
            {"prompt": "model rewrite"}, {"prompt": "another rewrite"}, {},
            {"include_past": False},
            {"municipality": "bileca", "date_from": None, "date_to": None},
        ]
        calls = [SimpleNamespace(type="function_call", name=name, call_id=f"call-{index}",
                                 arguments=json.dumps(args))
                 for index, (name, args) in enumerate(zip(names, arguments))]
        query = "Koje manifestacije su održane 2010?"
        processed = {"facts": ["Proveren rezultat"]}
        for path in ("/api/chat", "/api/chat/stream"):
            with self.subTest(path=path), ExitStack() as stack:
                provider = MagicMock()
                provider.responses.create.side_effect = [
                    SimpleNamespace(output=calls, output_text=""),
                    SimpleNamespace(output_text="Proveren odgovor"),
                ]
                stream = provider.responses.stream.return_value.__enter__.return_value
                stream.__iter__.return_value = iter([
                    SimpleNamespace(type="response.output_text.delta", delta="Proveren odgovor")])
                stream.get_final_response.return_value = SimpleNamespace(output_text="Proveren odgovor")
                stack.enter_context(patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}))
                stack.enter_context(patch("main.OpenAI", return_value=provider))
                stack.enter_context(patch("main.get_assistant_instructions", return_value="instructions"))
                stack.enter_context(patch("main.sanitize_tool_result", return_value=processed))
                tools = {name: stack.enter_context(patch(f"main.{name}", return_value={"raw": name}))
                         for name in names}
                writer = stack.enter_context(patch("main.record_chat_turn"))
                response = self.client.post(path, json={"query": query, "filters": {
                    "destinations": ["trebinje"]}})
                self.assertEqual(response.status_code, 200)
                writer.assert_called_once()
                saved = writer.call_args.kwargs
                self.assertEqual((saved["app_id"], saved["client_id"]), (66, 18))
                self.assertEqual(saved["query"], query)
                self.assertEqual(saved["assistant_text"], "Proveren odgovor")
                self.assertEqual(saved["filters"]["destinations"], ["trebinje"])
                runs = saved["tool_runs"]
                self.assertEqual([run["name"] for run in runs], names)
                self.assertEqual([run["call_id"] for run in runs], [f"call-{i}" for i in range(5)])
                self.assertEqual(runs[0]["arguments"], {"prompt": query})
                self.assertEqual(runs[1]["arguments"], {"prompt": query})
                self.assertEqual(runs[2]["arguments"], {})
                self.assertEqual(runs[3]["arguments"], {"include_past": True})
                self.assertEqual(runs[4]["arguments"], arguments[4])
                tools["get_tourism_events"].assert_called_once_with(include_past=True)
                tools["get_weather"].assert_called_once_with(**arguments[4])
                sent = (provider.responses.stream.call_args.kwargs if path.endswith("stream")
                        else provider.responses.create.call_args_list[-1].kwargs)
                outputs = [item["output"] for item in sent["input"]
                           if isinstance(item, dict) and item.get("type") == "function_call_output"]
                self.assertEqual([run["output"] for run in runs], outputs)
                self.assertTrue(all(json.loads(output) == processed for output in outputs))
                session = main._sessions[response.cookies[main.CHAT_SESSION_COOKIE]]
                self.assertTrue(all(message["role"] in {"user", "assistant"}
                                    for message in session.messages))

    def test_consecutive_requests_do_not_reuse_previous_tool_results(self):
        def answer(query, history=None, filters=None, catalogue_out=None, tool_runs_out=None):
            if query == "First":
                tool_runs_out.append({"name": "search_knowledge", "call_id": "first",
                                      "arguments": {"prompt": query}, "output": "{}"})
            return "Odgovor"

        with patch("main.answer_query", side_effect=answer), patch("main.record_chat_turn") as writer:
            self.client.post("/api/chat", json={"query": "First"})
            self.client.post("/api/chat", json={"query": "Second"})
        first, second = [call.kwargs for call in writer.call_args_list]
        self.assertEqual(first["thread_id"], second["thread_id"])
        self.assertEqual(len(first["tool_runs"]), 1)
        self.assertEqual(second["tool_runs"], [])

    def test_sql_outage_keeps_both_response_formats_and_in_memory_history(self):
        for path in ("/api/chat", "/api/chat/stream"):
            with self.subTest(path=path), \
                 patch("chat_persistence.sql_connection", side_effect=RuntimeError("offline")), \
                 patch("main.answer_query", return_value="Odgovor"), \
                 patch("main.answer_query_stream", return_value=iter(["Odgovor"])):
                response = self.client.post(path, json={"query": "Pitanje"})
                self.assertEqual(response.status_code, 200)
                if path.endswith("stream"):
                    final = json.loads(response.text.strip().split("\n\n")[-1][6:])
                    self.assertEqual(final, {"content": "Odgovor", "done": True})
                else:
                    self.assertEqual(response.json()["response"], "Odgovor")
                session = main._sessions[response.cookies[main.CHAT_SESSION_COOKIE]]
                self.assertEqual(session.latest_assistant_text, "Odgovor")

    def test_generation_failure_does_not_record_a_completed_turn_after_tool_use(self):
        def fail(query, history=None, filters=None, catalogue_out=None, tool_runs_out=None):
            tool_runs_out.append({"name": "search_knowledge", "call_id": "call-failed",
                                  "arguments": {"prompt": query}, "output": "{}"})
            raise RuntimeError("generation failed after tool use")

        def fail_stream(*args, **kwargs):
            fail(*args, **kwargs)
            yield "unreachable"

        for path in ("/api/chat", "/api/chat/stream"):
            with self.subTest(path=path), patch("main.answer_query", side_effect=fail), \
                 patch("main.answer_query_stream", side_effect=fail_stream), \
                 patch("main.record_chat_turn") as writer:
                response = self.client.post(path, json={"query": "Pitanje"})
                writer.assert_not_called()
                self.assertEqual(response.status_code, 200 if path.endswith("stream") else 502)
                self.assertTrue(all(not session.messages for session in main._sessions.values()))


if __name__ == "__main__":
    unittest.main()
