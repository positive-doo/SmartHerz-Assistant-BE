import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx

import main
import weather


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
LAST_MODIFIED = "Mon, 26 Oct 2026 11:00:00 GMT"


def sample():
    def row(stamp, temperature=12.0, wind=2.5, hours=1, rain=0.0):
        return {"time": stamp, "data": {
            "instant": {"details": {"air_temperature": temperature, "wind_speed": wind}},
            f"next_{hours}_hours": {"summary": {"symbol_code": "cloudy"}, "details": {"precipitation_amount": rain}},
        }}
    return {"properties": {"meta": {"updated_at": "2026-10-26T11:00:00Z",
                                     "units": {"air_temperature": "celsius", "wind_speed": "m/s", "precipitation_amount": "mm"}},
                           "timeseries": [row("2026-10-26T12:00:00Z"),
                                          row("2026-10-26T23:00:00Z", temperature=-2),
                                          row("2026-10-27T12:00:00Z", hours=6, rain=1.4),
                                          row("2026-10-28T00:00:00Z")]}}


def response(data=None, status=200, **headers):
    return httpx.Response(status, json=data if data is not None else sample(),
                          headers={"Expires": "Mon, 26 Oct 2026 13:00:00 GMT", "Last-Modified": LAST_MODIFIED, **headers},
                          request=httpx.Request("GET", weather.FORECAST_URL))


class WeatherTests(unittest.TestCase):
    def setUp(self):
        weather._cache.clear()
        self.clock = patch.object(weather, "datetime", wraps=datetime)
        clock = self.clock.start()
        clock.now.return_value = NOW
        self.addCleanup(self.clock.stop)
        self.addCleanup(weather._cache.clear)

    def test_all_eight_municipalities_use_their_own_verified_coordinates(self):
        with patch.object(weather.httpx, "get", return_value=response()) as fetch:
            for municipality, (name, latitude, longitude) in weather.LOCATIONS.items():
                with self.subTest(municipality=municipality):
                    result = weather.get_weather(municipality)
                    self.assertNotIn("error", result)
                    self.assertEqual(result["forecast_location"], name)
                    self.assertEqual(fetch.call_args.kwargs["params"], {"lat": latitude, "lon": longitude})
                    self.assertIn("smart-herz.modoos.rs", fetch.call_args.kwargs["headers"]["User-Agent"])
                    self.assertEqual(fetch.call_args.kwargs["timeout"], 10.0)
        self.assertEqual(len(weather._cache), 8)
        self.assertEqual(weather.LOCATIONS["istocni-mostar"][1:], (43.4685, 17.9832))

    def test_local_inclusive_dates_units_and_precipitation_intervals(self):
        with patch.object(weather.httpx, "get", return_value=response()):
            result = weather.get_weather("bileca", "2026-10-27", "2026-10-27")
        self.assertEqual(result["timezone"], "Europe/Sarajevo")
        self.assertEqual(len(result["forecast"]), 2)
        first, second = result["forecast"]
        self.assertEqual(first["time"], "2026-10-27T00:00:00+01:00")
        self.assertEqual(first["temperature_c"], -2.0)
        self.assertEqual(first["wind_speed_kmh"], 9.0)
        self.assertEqual(first["precipitation_mm"], 0.0)
        self.assertEqual(second["precipitation_period_hours"], 6)
        self.assertEqual(second["precipitation_until"], "2026-10-27T19:00:00+01:00")
        self.assertEqual(result["source"]["name"], "MET Norway")
        self.assertIn("by/4.0", result["source"]["license"])

    def test_missing_precipitation_is_not_reported_as_zero(self):
        data = sample()
        del data["properties"]["timeseries"][0]["data"]["next_1_hours"]
        with patch.object(weather.httpx, "get", return_value=response(data)):
            result = weather.get_weather("trebinje")
        self.assertNotIn("precipitation_mm", result["forecast"][0])

    def test_invalid_dates_and_unknown_municipality_do_not_contact_provider(self):
        with patch.object(weather.httpx, "get") as fetch:
            for start, end in [("2026-10-25", None), ("2026-11-02", None), ("2026-10-28", "2026-10-27"),
                               ("2026-02-30", None), ("20261026", None), ("2026-W44-1", None), ("today", None), (123, None)]:
                with self.subTest(start=start, end=end):
                    self.assertIn("error", weather.get_weather("trebinje", start, end))
            self.assertIn("error", weather.get_weather("https://example.com"))
            self.assertIn("error", weather.get_weather(None))
            fetch.assert_not_called()

    def test_provider_expiry_avoids_duplicate_requests_and_date_ranges_share_cache(self):
        with patch.object(weather.httpx, "get", return_value=response()) as fetch:
            first = weather.get_weather("trebinje")
            second = weather.get_weather("trebinje", "2026-10-27")
        fetch.assert_called_once()
        self.assertNotIn("error", first)
        self.assertNotIn("error", second)

    def test_expired_cache_uses_conditional_get_and_accepts_304(self):
        with patch.object(weather.httpx, "get", side_effect=[response(), response(status=304)]) as fetch:
            first = weather.get_weather("trebinje")
            weather._cache["trebinje"]["expires_at"] = NOW - timedelta(seconds=1)
            second = weather.get_weather("trebinje")
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.kwargs["headers"]["If-Modified-Since"], LAST_MODIFIED)

    def test_expired_cache_is_not_used_after_timeout(self):
        with patch.object(weather.httpx, "get", side_effect=[response(), httpx.ReadTimeout("test")]):
            self.assertNotIn("error", weather.get_weather("trebinje"))
            weather._cache["trebinje"]["expires_at"] = NOW - timedelta(seconds=1)
            result = weather.get_weather("trebinje")
        self.assertIn("error", result)
        self.assertEqual(result["forecast"], [])

    def test_http_failures_invalid_units_and_values_do_not_produce_a_forecast(self):
        invalid_units = sample()
        invalid_units["properties"]["meta"]["units"]["air_temperature"] = "fahrenheit"
        invalid_value = sample()
        invalid_value["properties"]["timeseries"][0]["data"]["instant"]["details"]["air_temperature"] = float("nan")
        negative_wind = sample()
        negative_wind["properties"]["timeseries"][0]["data"]["instant"]["details"]["wind_speed"] = -1
        for candidate in [response(status=403), response(status=429), response(status=503), response(status=203),
                          response({}), response(invalid_units), response(negative_wind)]:
            with self.subTest(status=candidate.status_code):
                weather._cache.clear()
                with patch.object(weather.httpx, "get", return_value=candidate):
                    self.assertIn("error", weather.get_weather("trebinje"))
        with patch.object(weather, "_forecast", return_value=invalid_value):
            self.assertIn("error", weather.get_weather("trebinje"))

    def test_local_midnight_does_not_use_yesterdays_utc_date(self):
        with patch.object(weather, "datetime", wraps=datetime) as clock, patch.object(weather.httpx, "get", return_value=response()):
            clock.now.side_effect = lambda tz: datetime(2026, 10, 26, 23, 30, tzinfo=timezone.utc).astimezone(tz)
            result = weather.get_weather("trebinje")
        self.assertEqual(result["date_from"], "2026-10-27")


class WeatherRoutingTests(unittest.TestCase):
    def test_json_and_stream_chat_dispatch_the_same_weather_arguments(self):
        arguments = {"municipality": "bileca", "date_from": "2026-10-27", "date_to": "2026-10-27"}
        call = SimpleNamespace(type="function_call", name="get_weather", call_id="weather", arguments=json.dumps(arguments))
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                client = MagicMock()
                initial = SimpleNamespace(output=[call], output_text="")
                client.responses.create.side_effect = [initial, SimpleNamespace(output_text="Verified weather")]
                stream = client.responses.stream.return_value.__enter__.return_value
                stream.__iter__.return_value = iter([SimpleNamespace(type="response.output_text.delta", delta="Verified weather")])
                stream.get_final_response.return_value = SimpleNamespace(output_text="Verified weather")
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                     patch.object(main, "OpenAI", return_value=client), \
                     patch.object(main, "get_assistant_instructions", return_value=weather.WEATHER_INSTRUCTIONS), \
                     patch.object(main, "get_weather", return_value={"forecast": [{"temperature_c": 12.0}]}) as tool:
                    answer = "".join(main.answer_query_stream("Vreme u Bileci sutra?")) if streaming else main.answer_query("Vreme u Bileci sutra?")
                self.assertEqual(answer, "Verified weather")
                tool.assert_called_once_with(**arguments)
                self.assertIn(weather.WEATHER_TOOL, client.responses.create.call_args_list[0].kwargs["tools"])
                sent = client.responses.stream.call_args.kwargs if streaming else client.responses.create.call_args_list[-1].kwargs
                self.assertEqual(sent["tool_choice"], "none")
                outputs = [r for r in sent["input"] if isinstance(r, dict) and r.get("type") == "function_call_output"]
                self.assertEqual(json.loads(outputs[0]["output"])["forecast"][0]["temperature_c"], 12.0)

    def test_unrelated_direct_answer_does_not_fetch_weather(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output=[], output_text="Zdravo!")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch.object(main, "OpenAI", return_value=client), \
             patch.object(main, "get_assistant_instructions", return_value=weather.WEATHER_INSTRUCTIONS), \
             patch.object(main, "get_weather") as tool:
            self.assertEqual(main.answer_query("Zdravo"), "Zdravo!")
        tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
