import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import main


class SmartHerzApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with main._sessions_lock:
            main._sessions.clear()
        main._prompt_cache = None
        self.client = TestClient(main.app, base_url="http://localhost")

    def test_health(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chat_contract_and_cookie_history(self) -> None:
        histories = []

        def fake_answer(query, history=None, filters=None):
            del filters
            histories.append(copy.deepcopy(history or []))
            return f"Odgovor: {query}"

        with patch("main.answer_query", side_effect=fake_answer):
            first = self.client.post("/api/chat", json={"query": "Prvo pitanje"})
            second = self.client.post("/api/chat", json={"query": "Drugo pitanje"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["response"], "Odgovor: Prvo pitanje")
        self.assertIn(main.CHAT_SESSION_COOKIE, first.cookies)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            histories[1],
            [
                {"role": "user", "content": "Prvo pitanje"},
                {"role": "assistant", "content": "Odgovor: Prvo pitanje"},
            ],
        )

    def test_chat_accepts_and_forwards_frontend_filters(self) -> None:
        captured_filters = []

        def fake_answer(query, history=None, filters=None):
            del query, history
            captured_filters.append(filters)
            return "Filtriran odgovor"

        with patch("main.answer_query", side_effect=fake_answer):
            response = self.client.post(
                "/api/chat",
                json={
                    "query": "Napravi plan putovanja",
                    "filters": {
                        "dateRange": {
                            "start": "2026-09-01",
                            "end": "2026-09-03",
                        },
                        "destinations": ["trebinje"],
                        "interests": ["wineries_tasting_rooms"],
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["response"], "Filtriran odgovor")
        self.assertEqual(captured_filters[0].destinations, ["trebinje"])
        self.assertEqual(captured_filters[0].interests, ["wineries_tasting_rooms"])
        self.assertEqual(captured_filters[0].dateRange.start, "2026-09-01")

    def test_structured_filters_are_added_to_model_instructions(self) -> None:
        response = SimpleNamespace(output=[], output_text="Plan je spreman.")
        responses = Mock()
        responses.create.return_value = response
        openai_client = Mock(responses=responses)
        filters = main.ChatFilters(
            dateRange=main.DateRangeFilter(
                start="2026-09-01",
                end="2026-09-03",
            ),
            destinations=["trebinje"],
            interests=["wineries_tasting_rooms"],
        )

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
            patch("main.get_assistant_instructions", return_value="instructions"),
        ):
            result = main.answer_query("Napravi plan", filters=filters)

        self.assertEqual(result, "Plan je spreman.")
        instructions = responses.create.call_args.kwargs["instructions"]
        self.assertIn('"destinations": ["trebinje"]', instructions)
        self.assertIn('"start": "2026-09-01"', instructions)

    def test_agrotour_function_receives_original_user_prompt(self) -> None:
        first_response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="search_agrotour",
                    call_id="call-1",
                    arguments='{"prompt":"rewritten prompt"}',
                )
            ],
            output_text="",
        )
        final_response = SimpleNamespace(output=[], output_text="Pronađen je restoran.")
        responses = Mock()
        responses.create.side_effect = [first_response, final_response]
        openai_client = Mock(responses=responses)
        user_prompt = "Preporuči mi restoran u Trebinju"

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
            patch("main.get_assistant_instructions", return_value="instructions"),
            patch("main.search_agrotour", return_value={"data": []}) as search,
        ):
            result = main.answer_query(user_prompt)

        self.assertEqual(result, "Pronađen je restoran.")
        search.assert_called_once_with(user_prompt)
        function_output = responses.create.call_args_list[1].kwargs["input"][-1]
        self.assertEqual(function_output["type"], "function_call_output")
        self.assertEqual(function_output["call_id"], "call-1")

    def test_tts_uses_latest_answer_without_frontend_changes(self) -> None:
        with patch("main.answer_query", return_value="Poslednji odgovor asistenta"):
            chat_response = self.client.post("/api/chat", json={"query": "Pitanje"})
        self.assertEqual(chat_response.status_code, 200)

        speech = Mock()
        speech.create.return_value = SimpleNamespace(content=b"ID3-test-audio")
        openai_client = Mock()
        openai_client.audio.speech = speech
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
        ):
            response = self.client.post(
                "/tts",
                headers={"Session-ID": "frontend-session"},
                json={"message_id": "frontend-generated-message-id"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"ID3-test-audio")
        self.assertEqual(speech.create.call_args.kwargs["input"], "Poslednji odgovor asistenta")

    def test_transcription_contract(self) -> None:
        transcriptions = Mock()
        transcriptions.create.return_value = SimpleNamespace(text="  Trebinje  ")
        openai_client = Mock()
        openai_client.audio.transcriptions = transcriptions
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
        ):
            response = self.client.post(
                "/transcribe",
                headers={"Session-ID": "frontend-session"},
                files={"blob": ("recording.webm", b"audio-bytes", "audio/webm")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"transcript": "Trebinje"})
        self.assertEqual(transcriptions.create.call_args.kwargs["language"], "sr")

    def test_feedback_contract(self) -> None:
        payload = {
            "sessionId": "frontend-session",
            "status": "Good",
            "feedback": "Korisno",
            "feedbackEmail": "",
            "lastQuestion": "Pitanje",
            "lastAnswer": "Odgovor",
        }
        with patch("main._insert_feedback", return_value=42) as insert:
            response = self.client.post("/feedback", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "feedback_id": 42})
        self.assertEqual(insert.call_args.args[0].lastQuestion, "Pitanje")

    def test_pdf_contract_and_unicode(self) -> None:
        response = self.client.post(
            "/save_pdf",
            data={
                "markdownText": "# Plan putovanja\n\n- Trebinje\n- Međugorje",
                "original_filename": "Moj plan.pdf",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn("Moj-plan.pdf", response.headers["content-disposition"])

    def test_initialize_session_matches_frontend_body(self) -> None:
        response = self.client.post(
            "/initialize_session",
            json={"session_id": "frontend-session-id"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"session_id": "frontend-session-id"})
        self.assertEqual(response.cookies[main.CHAT_SESSION_COOKIE], "frontend-session-id")


if __name__ == "__main__":
    unittest.main()
