import socket
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.schemas.source_document import SourceDocument
from app.tools.source_fetch import (
    SourceFetchError,
    canonicalize_url,
    fetch_source,
)


class SourceFetchTests(unittest.TestCase):
    def test_rejects_content_hash_mismatch(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "content_hash does not match",
        ):
            SourceDocument(
                requested_url="https://example.com/source",
                url="https://example.com/source",
                canonical_url=(
                    "https://example.com/source"
                ),
                content="Source text",
                content_hash="0" * 64,
                mime_type="text/plain",
            )

    def test_canonicalizes_url_and_removes_tracking(
        self,
    ) -> None:
        result = canonicalize_url(
            "HTTPS://Example.COM:443/path?"
            "utm_source=test&b=2&a=1#fragment"
        )

        self.assertEqual(
            result,
            "https://example.com/path?a=1&b=2",
        )

    def test_canonicalizes_ipv6_host(
        self,
    ) -> None:
        self.assertEqual(
            canonicalize_url(
                "https://[2001:4860:4860::8888]:443/"
            ),
            "https://[2001:4860:4860::8888]/",
        )

    def test_fetches_and_extracts_html_text(
        self,
    ) -> None:
        html = (
            "<html><head><title>Example title</title>"
            "<style>hidden css</style></head>"
            "<body><h1>Heading</h1>"
            "<p>Exact evidence quote.</p>"
            "<script>hidden script</script></body></html>"
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={
                    "content-type": (
                        "text/html; charset=utf-8"
                    )
                },
                content=html.encode(),
                request=request,
            )
        )

        with (
            httpx.Client(transport=transport) as client,
            patch(
                "app.tools.source_fetch._validate_public_url"
            ),
        ):
            document = fetch_source(
                "https://example.com/page",
                client=client,
            )

        self.assertEqual(
            document.title,
            "Example title",
        )
        self.assertIn(
            "Exact evidence quote.",
            document.content,
        )
        self.assertNotIn(
            "hidden script",
            document.content,
        )
        self.assertEqual(
            len(document.content_hash),
            64,
        )

    def test_validates_every_redirect_target(
        self,
    ) -> None:
        requested_urls: list[str] = []
        validated_urls: list[str] = []

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            requested_urls.append(str(request.url))

            if request.url.path == "/start":
                return httpx.Response(
                    302,
                    headers={
                        "location": "/final",
                    },
                    request=request,
                )

            return httpx.Response(
                200,
                headers={
                    "content-type": "text/plain",
                },
                content=b"Final source text",
                request=request,
            )

        transport = httpx.MockTransport(handler)

        with (
            httpx.Client(transport=transport) as client,
            patch(
                "app.tools.source_fetch._validate_public_url",
                side_effect=validated_urls.append,
            ),
        ):
            document = fetch_source(
                "https://example.com/start",
                client=client,
            )

        self.assertEqual(
            requested_urls,
            [
                "https://example.com/start",
                "https://example.com/final",
            ],
        )
        self.assertEqual(
            validated_urls,
            requested_urls,
        )
        self.assertEqual(
            document.url,
            "https://example.com/final",
        )

    def test_rejects_response_over_size_limit(
        self,
    ) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "content-length": "100",
                },
                content=b"x" * 100,
                request=request,
            )
        )

        with (
            httpx.Client(transport=transport) as client,
            patch(
                "app.tools.source_fetch._validate_public_url"
            ),
        ):
            with self.assertRaisesRegex(
                SourceFetchError,
                "maximum download size",
            ):
                fetch_source(
                    "https://example.com/large",
                    client=client,
                    max_download_bytes=10,
                )

    def test_rejects_private_address(
        self,
    ) -> None:
        private_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            ("127.0.0.1", 80),
        )

        with patch(
            "app.tools.source_fetch.socket.getaddrinfo",
            return_value=[private_address],
        ):
            with self.assertRaisesRegex(
                SourceFetchError,
                "non-public address",
            ):
                fetch_source(
                    "http://internal.example/private"
                )


if __name__ == "__main__":
    unittest.main()
