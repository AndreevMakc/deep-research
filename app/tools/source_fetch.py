from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.schemas.source_document import SourceDocument


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_DOWNLOAD_BYTES = 5_000_000
MAX_REDIRECTS = 5

REDIRECT_STATUSES = {
    301,
    302,
    303,
    307,
    308,
}
TRACKING_QUERY_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


class SourceFetchError(RuntimeError):
    """Raised when a source cannot be fetched safely."""


class _HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    IGNORED_TAGS = {
        "noscript",
        "script",
        "style",
        "svg",
        "template",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self._text_parts: list[str] = []
        self._title_parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    @property
    def title(self) -> str | None:
        title = _normalize_text(
            "".join(self._title_parts)
        )
        return title or None

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:
        normalized_tag = tag.lower()

        if normalized_tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
            return

        if self._ignored_depth:
            return

        if normalized_tag == "title":
            self._in_title = True

        if normalized_tag in self.BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()

        if normalized_tag in self.IGNORED_TAGS:
            self._ignored_depth = max(
                0,
                self._ignored_depth - 1,
            )
            return

        if self._ignored_depth:
            return

        if normalized_tag == "title":
            self._in_title = False

        if normalized_tag in self.BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return

        self._text_parts.append(data)

        if self._in_title:
            self._title_parts.append(data)


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https"}:
        raise ValueError(
            "Only HTTP(S) source URLs are supported"
        )

    if not parsed.hostname:
        raise ValueError(
            "Source URL must contain a hostname"
        )

    if parsed.username or parsed.password:
        raise ValueError(
            "Source URL must not contain credentials"
        )

    host = parsed.hostname.lower().rstrip(".")
    netloc_host = (
        f"[{host}]"
        if ":" in host
        else host
    )
    port = parsed.port

    if (
        port is not None
        and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        )
    ):
        netloc = f"{netloc_host}:{port}"
    else:
        netloc = netloc_host

    query_items = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if (
            not key.lower().startswith("utm_")
            and key.lower()
            not in TRACKING_QUERY_PARAMETERS
        )
    ]
    query_items.sort()

    path = parsed.path or "/"

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            urlencode(query_items, doseq=True),
            "",
        )
    )


def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise SourceFetchError(
            "Only HTTP(S) source URLs are allowed"
        )

    if parsed.username or parsed.password:
        raise SourceFetchError(
            "Source URLs with credentials are not allowed"
        )

    hostname = parsed.hostname

    if not hostname:
        raise SourceFetchError(
            "Source URL must contain a hostname"
        )

    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise SourceFetchError(
            f"Cannot resolve source hostname: {hostname}"
        ) from error

    for address in addresses:
        ip_value = ipaddress.ip_address(
            address[4][0]
        )

        if not ip_value.is_global:
            raise SourceFetchError(
                "Source URL resolves to a non-public address"
            )


def _normalize_text(value: str) -> str:
    lines = []

    for raw_line in value.replace(
        "\r\n",
        "\n",
    ).replace("\r", "\n").splitlines():
        line = re.sub(r"[^\S\n]+", " ", raw_line).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


def _decode_text(
    body: bytes,
    encoding: str | None,
) -> str:
    return body.decode(
        encoding or "utf-8",
        errors="replace",
    )


def _extract_document(
    body: bytes,
    mime_type: str,
    encoding: str | None,
) -> tuple[str, str | None, dict]:
    if mime_type in {
        "text/html",
        "application/xhtml+xml",
    }:
        parser = _HTMLTextExtractor()
        parser.feed(_decode_text(body, encoding))
        return (
            _normalize_text(parser.text),
            parser.title,
            {},
        )

    if (
        mime_type.startswith("text/")
        or mime_type
        in {
            "application/json",
            "application/xml",
            "application/rss+xml",
        }
    ):
        return (
            _normalize_text(
                _decode_text(body, encoding)
            ),
            None,
            {},
        )

    if mime_type == "application/pdf":
        reader = PdfReader(BytesIO(body))
        pages = [
            page.extract_text() or ""
            for page in reader.pages
        ]
        metadata = reader.metadata
        title = (
            str(metadata.title).strip()
            if metadata and metadata.title
            else None
        )
        return (
            _normalize_text("\n".join(pages)),
            title,
            {
                "page_count": len(reader.pages),
            },
        )

    raise SourceFetchError(
        f"Unsupported source MIME type: {mime_type}"
    )


def _read_response_body(
    response: httpx.Response,
    max_download_bytes: int,
) -> bytes:
    raw_length = response.headers.get(
        "content-length"
    )

    if raw_length:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = None

        if (
            content_length is not None
            and content_length > max_download_bytes
        ):
            raise SourceFetchError(
                "Source exceeds maximum download size"
            )

    body = bytearray()

    for chunk in response.iter_bytes():
        body.extend(chunk)

        if len(body) > max_download_bytes:
            raise SourceFetchError(
                "Source exceeds maximum download size"
            )

    return bytes(body)


def fetch_source(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> SourceDocument:
    requested_url = url.strip()
    current_url = requested_url
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=timeout_seconds,
        headers={
            "User-Agent": (
                "deep-research-v1/1.0 "
                "(research source fetcher)"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/pdf,text/plain;q=0.9,*/*;q=0.1"
            ),
        },
    )

    try:
        for redirect_count in range(
            MAX_REDIRECTS + 1
        ):
            _validate_public_url(current_url)

            with active_client.stream(
                "GET",
                current_url,
                follow_redirects=False,
            ) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get(
                        "location"
                    )

                    if not location:
                        raise SourceFetchError(
                            "Redirect response has no Location"
                        )

                    if redirect_count >= MAX_REDIRECTS:
                        raise SourceFetchError(
                            "Source has too many redirects"
                        )

                    current_url = urljoin(
                        str(response.url),
                        location,
                    )
                    continue

                response.raise_for_status()
                body = _read_response_body(
                    response,
                    max_download_bytes,
                )
                final_url = str(response.url)
                content_type = response.headers.get(
                    "content-type",
                    "application/octet-stream",
                )
                mime_type = (
                    content_type.split(";", 1)[0]
                    .strip()
                    .lower()
                )

                if body.startswith(b"%PDF-"):
                    mime_type = "application/pdf"
                elif (
                    mime_type == "application/octet-stream"
                    and (
                        b"<html" in body[:1000].lower()
                        or b"<!doctype html"
                        in body[:1000].lower()
                    )
                ):
                    mime_type = "text/html"

                content, title, metadata = (
                    _extract_document(
                        body=body,
                        mime_type=mime_type,
                        encoding=response.encoding,
                    )
                )

                if not content:
                    raise SourceFetchError(
                        "Source contains no extractable text"
                    )

                return SourceDocument(
                    requested_url=requested_url,
                    url=final_url,
                    canonical_url=canonicalize_url(
                        final_url
                    ),
                    title=title,
                    content=content,
                    content_hash=hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                    mime_type=mime_type,
                    http_status=response.status_code,
                    metadata_json={
                        **metadata,
                        "downloaded_bytes": len(body),
                    },
                )

        raise SourceFetchError(
            "Source has too many redirects"
        )
    except httpx.HTTPError as error:
        raise SourceFetchError(
            f"Failed to download source: {error}"
        ) from error
    except PdfReadError as error:
        raise SourceFetchError(
            f"Failed to parse PDF source: {error}"
        ) from error
    finally:
        if owns_client:
            active_client.close()
