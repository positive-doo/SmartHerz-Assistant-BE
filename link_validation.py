import re
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
TRAILING_PUNCTUATION = ".,;:!?)]}"
_REMOVE = object()
_LINK_CHECKS = ThreadPoolExecutor(max_workers=6, thread_name_prefix="link-check")


def _returns_404(url: str) -> bool:
    try:
        request = Request(
            url,
            headers={
                "Range": "bytes=0-0",
                "User-Agent": "SmartHerz-Assistant-BE/0.2",
            },
        )
        with urlopen(request, timeout=4):
            return False
    except HTTPError as response:
        with response:
            return response.code == 404
    except (OSError, ValueError):
        return False


def _find_urls(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _find_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _find_urls(item)
    elif isinstance(value, str):
        for match in URL_PATTERN.finditer(value):
            yield match.group(0).rstrip(TRAILING_PUNCTUATION)


def start_link_checks(value, pending=None):
    if pending is None:
        pending = {}
    for url in _find_urls(value):
        if url not in pending:
            pending[url] = _LINK_CHECKS.submit(_returns_404, url)
    return pending


def sanitize_tool_result(value, *, pending=None):
    checks = start_link_checks(value, pending)
    missing = {url for url, check in checks.items() if check.result()}

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
            return suffix if url in missing else raw_url

        cleaned = URL_PATTERN.sub(replace, item).strip()
        return cleaned if cleaned else _REMOVE

    cleaned_result = sanitize(value)
    return None if cleaned_result is _REMOVE else cleaned_result
