import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

import link_validation


class LinkValidationTests(unittest.TestCase):
    def test_nested_404_links_are_removed_and_duplicates_checked_once(self) -> None:
        payload = {
            "website": "https://example.test/missing",
            "gallery": [
                "https://example.test/working",
                "https://example.test/missing",
            ],
            "description": "Detalji: https://example.test/missing.",
            "title": "Trebinje",
        }

        with patch(
            "link_validation._returns_404",
            side_effect=lambda url: url.endswith("/missing"),
        ) as check:
            result = link_validation.sanitize_tool_result(payload)

        self.assertNotIn("website", result)
        self.assertEqual(result["gallery"], ["https://example.test/working"])
        self.assertEqual(result["description"], "Detalji: .")
        self.assertEqual(result["title"], "Trebinje")
        self.assertEqual(check.call_count, 2)

    def test_only_confirmed_404_is_rejected(self) -> None:
        def response_for(request, timeout):
            del timeout
            url = request.full_url
            if url.endswith("/missing"):
                raise HTTPError(url, 404, "Not Found", {}, None)
            if url.endswith("/forbidden"):
                raise HTTPError(url, 403, "Forbidden", {}, None)
            if url.endswith("/offline"):
                raise URLError("offline")
            context = MagicMock()
            context.__enter__.return_value = context
            return context

        with patch("link_validation.urlopen", side_effect=response_for):
            self.assertTrue(link_validation._returns_404("https://example.test/missing"))
            self.assertFalse(link_validation._returns_404("https://example.test/forbidden"))
            self.assertFalse(link_validation._returns_404("https://example.test/offline"))
            self.assertFalse(link_validation._returns_404("https://example.test/working"))

    def test_non_http_values_do_not_trigger_checks(self) -> None:
        payload = {
            "email": "mailto:info@example.test",
            "path": "/relative/path",
            "title": "No link here",
        }

        with patch("link_validation._returns_404") as check:
            result = link_validation.sanitize_tool_result(payload)

        self.assertEqual(result, payload)
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
