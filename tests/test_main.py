import copy
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from lxml import etree

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

    def test_knowledge_function_receives_original_user_prompt(self) -> None:
        first_response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="search_knowledge",
                    call_id="call-knowledge",
                    arguments='{"prompt":"rewritten prompt"}',
                )
            ],
            output_text="",
        )
        final_response = SimpleNamespace(output=[], output_text="Trebinje ima Stari grad.")
        responses = Mock()
        responses.create.side_effect = [first_response, final_response]
        openai_client = Mock(responses=responses)
        user_prompt = "Šta obići u Trebinju?"

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
            patch("main.get_assistant_instructions", return_value="instructions"),
            patch("main.search_knowledge", return_value={"results": []}) as search,
        ):
            result = main.answer_query(user_prompt)

        self.assertEqual(result, "Trebinje ima Stari grad.")
        search.assert_called_once_with(user_prompt)
        function_output = responses.create.call_args_list[1].kwargs["input"][-1]
        self.assertEqual(function_output["call_id"], "call-knowledge")

    def test_tool_results_are_sanitized_before_model_synthesis(self) -> None:
        first_response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="search_agrotour",
                    call_id="call-links",
                    arguments="{}",
                )
            ],
            output_text="",
        )
        final_response = SimpleNamespace(output=[], output_text="Provjeren odgovor.")
        responses = Mock()
        responses.create.side_effect = [first_response, final_response]
        openai_client = Mock(responses=responses)

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
            patch("main.get_assistant_instructions", return_value="instructions"),
            patch(
                "main.search_agrotour",
                return_value={"website": "https://example.test/missing"},
            ),
            patch("main.sanitize_tool_result", return_value={}) as sanitize,
        ):
            main.answer_query("Preporuči restoran")

        sanitize.assert_called_once_with({"website": "https://example.test/missing"})
        function_output = responses.create.call_args_list[1].kwargs["input"][-1]
        self.assertEqual(json.loads(function_output["output"]), {})

    def test_answer_query_stream_yields_openai_deltas_after_tool_use(self) -> None:
        first_response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="search_agrotour",
                    call_id="call-stream",
                    arguments="{}",
                )
            ],
            output_text="",
        )

        class FakeStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def __iter__(self):
                return iter(
                    [
                        SimpleNamespace(type="response.output_text.delta", delta="Prvi"),
                        SimpleNamespace(type="response.output_text.delta", delta=" dio"),
                    ]
                )

            def get_final_response(self):
                return SimpleNamespace(output_text="Prvi dio")

        responses = Mock()
        responses.create.return_value = first_response
        responses.stream.return_value = FakeStream()
        openai_client = Mock(responses=responses)

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("main.OpenAI", return_value=openai_client),
            patch("main.get_assistant_instructions", return_value="instructions"),
            patch("main.search_agrotour", return_value={"data": []}),
        ):
            deltas = list(main.answer_query_stream("Preporuči restoran"))

        self.assertEqual(deltas, ["Prvi", " dio"])
        self.assertEqual(responses.stream.call_args.kwargs["tool_choice"], "none")

    def test_stream_endpoint_emits_cumulative_sse_and_saves_history(self) -> None:
        with patch("main.answer_query_stream", return_value=iter(["Prvi", " dio"])):
            response = self.client.post(
                "/api/chat/stream",
                json={
                    "query": "Pitanje",
                    "filters": {"destinations": ["trebinje"]},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = [
            json.loads(block.removeprefix("data: "))
            for block in response.text.strip().split("\n\n")
        ]
        self.assertEqual(
            events,
            [
                {"content": "Prvi", "done": False},
                {"content": "Prvi dio", "done": False},
                {"content": "Prvi dio", "done": True},
            ],
        )
        session_id = response.cookies[main.CHAT_SESSION_COOKIE]
        session = main._sessions[session_id]
        self.assertEqual(session.latest_assistant_text, "Prvi dio")
        self.assertEqual(session.messages[-1]["content"], "Prvi dio")

    def test_stream_endpoint_accepts_frontend_quiz_context(self) -> None:
        query = "Pitanje\n\nKontekst kviza putnika:\n" + "x" * 600

        with patch("main.answer_query_stream", return_value=iter(["Odgovor"])):
            response = self.client.post("/api/chat/stream", json={"query": query})

        self.assertEqual(response.status_code, 200)

    def test_stream_endpoint_reports_failure_without_saving_partial_text(self) -> None:
        def failing_stream(*args, **kwargs):
            del args, kwargs
            yield "Djelimično"
            raise RuntimeError("provider failed")

        with patch("main.answer_query_stream", side_effect=failing_stream):
            response = self.client.post(
                "/api/chat/stream",
                json={"query": "Pitanje"},
            )

        events = [
            json.loads(block.removeprefix("data: "))
            for block in response.text.strip().split("\n\n")
        ]
        self.assertEqual(events[0], {"content": "Djelimično", "done": False})
        self.assertEqual(events[-1]["done"], True)
        self.assertIn("error", events[-1])
        session_id = response.cookies[main.CHAT_SESSION_COOKIE]
        self.assertEqual(main._sessions[session_id].messages, [])

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
        def fake_convert(docx_path, output_directory, profile_directory):
            del profile_directory
            self.assertTrue(docx_path.is_file())
            pdf_path = output_directory / f"{docx_path.stem}.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%%EOF")
            return pdf_path

        with patch("main._convert_docx_to_pdf", side_effect=fake_convert):
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

    def test_word_template_content_styles_and_header_are_preserved(self) -> None:
        markdown_text = """# Dan 1 – Stari grad i centar Trebinja

## Jutro

- Šetnja kroz Stari grad Kastel
- Obilazak Trga slobode

## Popodne

- Šetnja uz Trebišnjicu

Praktično: Većinu centra možete obići pješke.
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "plan.docx"
            main._write_travel_plan_docx(markdown_text, output_path)
            document = main.Document(str(output_path))

            self.assertEqual(
                [
                    (paragraph.text, paragraph.style.name)
                    for paragraph in document.paragraphs
                ],
                [
                    ("Dan 1 – Stari grad i centar Trebinja", "SmartHerz Dan"),
                    ("Jutro", "SmartHerz Period"),
                    ("Šetnja kroz Stari grad Kastel", "SmartHerz Bullets"),
                    ("Obilazak Trga slobode", "SmartHerz Bullets"),
                    ("Popodne", "SmartHerz Period"),
                    ("Šetnja uz Trebišnjicu", "SmartHerz Bullets"),
                    (
                        "Praktično: Većinu centra možete obići pješke.",
                        "Normal",
                    ),
                ],
            )
            with (
                zipfile.ZipFile(main.TRAVEL_PLAN_TEMPLATE_PATH) as template_zip,
                zipfile.ZipFile(output_path) as output_zip,
            ):
                self.assertEqual(
                    etree.tostring(
                        etree.fromstring(output_zip.read("word/header1.xml")),
                        method="c14n",
                    ),
                    etree.tostring(
                        etree.fromstring(template_zip.read("word/header1.xml")),
                        method="c14n",
                    ),
                )
                self.assertEqual(
                    output_zip.read("word/media/image1.png"),
                    template_zip.read("word/media/image1.png"),
                )

    def test_word_inline_markdown_and_real_hyperlink(self) -> None:
        markdown_text = (
            "Obiđite **Stari grad**, _Trebišnjicu_ i "
            "[turistički portal](https://example.com)."
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "inline.docx"
            main._write_travel_plan_docx(markdown_text, output_path)
            document = main.Document(str(output_path))
            paragraph = document.paragraphs[0]

            self.assertTrue(
                any(
                    run.text == "Stari grad" and run.bold
                    for run in paragraph.runs
                )
            )
            self.assertTrue(
                any(
                    run.text == "Trebišnjicu" and run.italic
                    for run in paragraph.runs
                )
            )
            relationships = document.part.rels.values()
            self.assertTrue(
                any(
                    relationship.target_ref == "https://example.com"
                    for relationship in relationships
                )
            )

    def test_libreoffice_conversion_uses_isolated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            docx_path = work_directory / "travel-plan.docx"
            docx_path.write_bytes(b"test-docx")

            def fake_run(command, **kwargs):
                self.assertEqual(command[0], "libreoffice")
                self.assertEqual(kwargs["timeout"], 90)
                self.assertIn("--headless", command)
                self.assertIn("--nologo", command)
                self.assertIn("--nodefault", command)
                self.assertIn("--norestore", command)
                self.assertIn("--convert-to", command)
                self.assertIn("pdf", command)
                self.assertTrue(
                    any(
                        argument.startswith("-env:UserInstallation=file:")
                        for argument in command
                    )
                )
                pdf_path = work_directory / "travel-plan.pdf"
                pdf_path.write_bytes(b"%PDF-1.7\n%%EOF")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch("main.os.name", "posix"),
                patch("main.subprocess.run", side_effect=fake_run),
            ):
                result = main._convert_docx_to_pdf(
                    docx_path,
                    work_directory,
                    work_directory / "profile",
                )

            self.assertEqual(result.read_bytes(), b"%PDF-1.7\n%%EOF")

    def test_windows_conversion_uses_docx2pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            docx_path = work_directory / "travel-plan.docx"
            docx_path.write_bytes(b"test-docx")

            def fake_convert(source, destination):
                self.assertEqual(source, str(docx_path))
                self.assertEqual(
                    destination,
                    str(work_directory / "travel-plan.pdf"),
                )
                (work_directory / "travel-plan.pdf").write_bytes(
                    b"%PDF-1.7\n%%EOF"
                )

            with (
                patch("main.os.name", "nt"),
                patch("docx2pdf.convert", side_effect=fake_convert) as convert,
                patch("main.subprocess.run") as subprocess_run,
            ):
                result = main._convert_docx_to_pdf(
                    docx_path,
                    work_directory,
                    work_directory / "profile",
                )

            convert.assert_called_once()
            subprocess_run.assert_not_called()
            self.assertEqual(result.read_bytes(), b"%PDF-1.7\n%%EOF")

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
