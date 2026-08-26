import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
TRAILING_PUNCTUATION = ".,;:!?)]}"
_REMOVE = object()


def _returns_404(url: str) -> bool:
    request = Request(
        url,
        headers={
            "Range": "bytes=0-0",
            "User-Agent": "SmartHerz-Assistant-BE/0.2",
        },
    )
    try:
        with urlopen(request, timeout=4):
            return False
    except HTTPError as exc:
        return exc.code == 404
    except (URLError, TimeoutError, ValueError):
        return False


def sanitize_tool_result(value):
    checked: dict[str, bool] = {}

    def sanitize(item):
        if isinstance(item, dict):
            return {
                key: cleaned
                for key, nested in item.items()
                if (cleaned := sanitize(nested)) is not _REMOVE
            }
        if isinstance(item, list):
            return [cleaned for nested in item if (cleaned := sanitize(nested)) is not _REMOVE]
        if not isinstance(item, str):
            return item

        def replace(match: re.Match) -> str:
            raw_url = match.group(0)
            url = raw_url.rstrip(TRAILING_PUNCTUATION)
            suffix = raw_url[len(url) :]
            if url not in checked:
                checked[url] = _returns_404(url)
            return suffix if checked[url] else raw_url

        cleaned = URL_PATTERN.sub(replace, item).strip()
        return cleaned if cleaned else _REMOVE

    cleaned_result = sanitize(value)
    return None if cleaned_result is _REMOVE else cleaned_result
