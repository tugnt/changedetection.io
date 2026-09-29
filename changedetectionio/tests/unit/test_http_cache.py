from types import SimpleNamespace
from unittest.mock import patch

from requests.structures import CaseInsensitiveDict

from changedetectionio.content_fetchers.requests import fetcher as RequestsFetcher
from changedetectionio.processors.base import difference_detection_processor


class FakeResponse:
    def __init__(self, status_code, content=b'', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = CaseInsensitiveDict(headers or {})
        self.text = content.decode('utf-8')
        self.is_redirect = False


class FakeWatch(dict):
    was_edited = False


def run_fetch(fetcher, headers=None):
    fetcher._run_sync(
        url='https://example.test/product',
        timeout=5,
        request_headers=CaseInsensitiveDict(headers or {}),
        request_body=None,
        request_method='GET',
        current_include_filters=None,
        empty_pages_are_a_change=False,
        watch_uuid='watch-1',
    )


def test_requests_fetcher_reuses_etag_and_handles_304():
    first_response = FakeResponse(
        200,
        b'<html><body>Out of stock</body></html>',
        {'Content-Type': 'text/html; charset=utf-8', 'ETag': '"v1"'},
    )
    not_modified_response = FakeResponse(
        304,
        headers={'ETag': '"v1"'},
    )
    changed_response = FakeResponse(
        200,
        b'<html><body>In stock</body></html>',
        {'Content-Type': 'text/html; charset=utf-8', 'ETag': '"v2"'},
    )

    with patch(
        'changedetectionio.content_fetchers.requests.is_fetch_url_allowed',
        return_value=(True, ''),
    ), patch(
        'requests.Session.request',
        side_effect=[first_response, not_modified_response, changed_response],
    ) as request:
        first = RequestsFetcher()
        first.http_cache_key = 'cache-key'
        run_fetch(first)

        second = RequestsFetcher()
        second.http_cache_key = 'cache-key'
        second.http_cache_etag = first.http_cache_etag
        run_fetch(second, {'If-None-Match': first.http_cache_etag})

        third = RequestsFetcher()
        third.http_cache_key = 'cache-key'
        third.http_cache_etag = second.http_cache_etag
        run_fetch(third, {'If-None-Match': second.http_cache_etag})

    assert first.content == '<html><body>Out of stock</body></html>'
    assert first.http_cache_etag == '"v1"'
    assert second.status_code == 304
    assert second.not_modified is True
    assert second.content is None
    assert third.content == '<html><body>In stock</body></html>'
    assert third.not_modified is False
    assert third.http_cache_etag == '"v2"'
    assert request.call_args_list[1].kwargs['headers']['If-None-Match'] == '"v1"'
    assert request.call_args_list[2].kwargs['headers']['If-None-Match'] == '"v1"'


def test_http_cache_configuration_uses_key_and_skips_edited_watch():
    processor = object.__new__(difference_detection_processor)
    processor.fetcher = RequestsFetcher()
    processor.watch = FakeWatch(http_cache={})

    processor._configure_http_cache(
        prefer_fetch_backend='html_requests',
        url='https://example.test/product',
        request_method='GET',
        request_body=None,
        request_headers=CaseInsensitiveDict({'User-Agent': 'test'}),
        preferred_proxy_id='proxy-1',
    )

    cache_state = {
        'key': processor.fetcher.http_cache_key,
        'etag': '"v1"',
        'last_modified': None,
    }
    processor.watch['http_cache'] = cache_state
    processor.fetcher = RequestsFetcher()

    request_headers = CaseInsensitiveDict({'User-Agent': 'test'})
    processor._configure_http_cache(
        prefer_fetch_backend='html_requests',
        url='https://example.test/product',
        request_method='GET',
        request_body=None,
        request_headers=request_headers,
        preferred_proxy_id='proxy-1',
    )

    assert request_headers['If-None-Match'] == '"v1"'

    processor.watch.was_edited = True
    edited_headers = CaseInsensitiveDict({'User-Agent': 'test'})
    processor._configure_http_cache(
        prefer_fetch_backend='html_requests',
        url='https://example.test/product',
        request_method='GET',
        request_body=None,
        request_headers=edited_headers,
        preferred_proxy_id='proxy-1',
    )

    assert 'If-None-Match' not in edited_headers


def test_requests_fetcher_reuses_last_modified_when_etag_is_missing():
    first_response = FakeResponse(
        200,
        b'<html><body>Out of stock</body></html>',
        {
            'Content-Type': 'text/html; charset=utf-8',
            'Last-Modified': 'Sat, 27 Sep 2026 12:00:00 GMT',
        },
    )
    not_modified_response = FakeResponse(304)

    with patch(
        'changedetectionio.content_fetchers.requests.is_fetch_url_allowed',
        return_value=(True, ''),
    ), patch(
        'requests.Session.request',
        side_effect=[first_response, not_modified_response],
    ) as request:
        first = RequestsFetcher()
        first.http_cache_key = 'cache-key'
        run_fetch(first)

        second = RequestsFetcher()
        second.http_cache_key = 'cache-key'
        second.http_cache_last_modified = first.http_cache_last_modified
        run_fetch(second, {'If-Modified-Since': first.http_cache_last_modified})

    assert first.http_cache_etag is None
    assert first.http_cache_last_modified == 'Sat, 27 Sep 2026 12:00:00 GMT'
    assert second.not_modified is True
    assert second.http_cache_last_modified == first.http_cache_last_modified
    assert request.call_args_list[1].kwargs['headers']['If-Modified-Since'] == first.http_cache_last_modified


def test_http_cache_state_is_not_available_for_non_get_requests():
    processor = object.__new__(difference_detection_processor)
    processor.fetcher = RequestsFetcher()
    processor.watch = SimpleNamespace(get=lambda key, default=None: default, was_edited=False)

    processor._configure_http_cache(
        prefer_fetch_backend='html_requests',
        url='https://example.test/product',
        request_method='POST',
        request_body='payload',
        request_headers=CaseInsensitiveDict(),
        preferred_proxy_id='proxy-1',
    )

    assert processor.fetcher.http_cache_key is None
    assert processor.fetcher.get_http_cache_state() is None


def test_curl_cffi_is_opt_in_and_uses_requests_when_disabled():
    response = FakeResponse(
        200,
        b'<html><body>Plain requests</body></html>',
        {'Content-Type': 'text/html; charset=utf-8'},
    )

    with patch(
        'changedetectionio.content_fetchers.requests.is_fetch_url_allowed',
        return_value=(True, ''),
    ), patch(
        'requests.Session.request',
        return_value=response,
    ) as request, patch(
        'curl_cffi.requests.Session',
    ) as curl_session:
        fetcher = RequestsFetcher(curl_cffi_enabled=False)
        run_fetch(fetcher)

    request.assert_called_once()
    curl_session.assert_not_called()
    assert fetcher.content == '<html><body>Plain requests</body></html>'


def test_curl_cffi_uses_selected_impersonation_and_proxy():
    response = FakeResponse(
        200,
        b'<html><body>curl cffi</body></html>',
        {'Content-Type': 'text/html; charset=utf-8'},
    )

    with patch(
        'changedetectionio.content_fetchers.requests.is_fetch_url_allowed',
        return_value=(True, ''),
    ), patch('curl_cffi.requests.Session') as curl_session:
        curl_session.return_value.request.return_value = response
        fetcher = RequestsFetcher(
            curl_cffi_enabled=True,
            curl_cffi_impersonate='chrome136',
            proxy_override='http://proxy.example:8080',
        )
        run_fetch(fetcher)

    curl_session.assert_called_once_with(impersonate='chrome136')
    call_kwargs = curl_session.return_value.request.call_args.kwargs
    assert call_kwargs['proxies'] == {
        'http': 'http://proxy.example:8080',
        'https': 'http://proxy.example:8080',
        'ftp': 'http://proxy.example:8080',
    }
    assert fetcher.content == '<html><body>curl cffi</body></html>'


def test_curl_cffi_initialization_failure_falls_back_to_requests():
    response = FakeResponse(
        200,
        b'<html><body>fallback</body></html>',
        {'Content-Type': 'text/html; charset=utf-8'},
    )

    with patch(
        'changedetectionio.content_fetchers.requests.is_fetch_url_allowed',
        return_value=(True, ''),
    ), patch(
        'curl_cffi.requests.Session',
        side_effect=RuntimeError('unsupported impersonation'),
    ), patch(
        'requests.Session.request',
        return_value=response,
    ) as request:
        fetcher = RequestsFetcher(
            curl_cffi_enabled=True,
            curl_cffi_impersonate='unknown-browser',
        )
        run_fetch(fetcher)

    request.assert_called_once()
    assert fetcher.content == '<html><body>fallback</body></html>'