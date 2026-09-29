from flask_babel import lazy_gettext as _l
from loguru import logger
from urllib.parse import urljoin, urlparse
import hashlib
import os
import re
import asyncio
import time

from changedetectionio import strtobool
from changedetectionio.content_fetchers.exceptions import BrowserStepsInUnsupportedFetcher, EmptyReply, Non200ErrorCodeReceived
from changedetectionio.content_fetchers.base import Fetcher
from changedetectionio.validate_url import is_fetch_url_allowed, is_private_hostname, is_url_private_or_parser_confused


# "html_requests" is listed as the default fetcher in store.py!
class fetcher(Fetcher):
    fetcher_description = _l("Basic fast Plaintext/HTTP Client")

    def __init__(self, proxy_override=None, custom_browser_connection_url=None, **kwargs):
        super().__init__(**kwargs)
        self.proxy_override = proxy_override
        self.curl_cffi_enabled = kwargs.get('curl_cffi_enabled', False)
        self.curl_cffi_impersonate = kwargs.get('curl_cffi_impersonate', 'chrome120') or 'chrome120'
        # browser_connection_url is none because its always 'launched locally'

    def _run_sync(self,
            url,
            timeout,
            request_headers,
            request_body,
            request_method,
            ignore_status_codes=False,
            current_include_filters=None,
            is_binary=False,
            empty_pages_are_a_change=False,
            watch_uuid=None,
            ):
        """Synchronous version of run - the original requests implementation"""

        import chardet
        import requests

        if self.browser_steps:
            raise BrowserStepsInUnsupportedFetcher(url=url)

        proxies = {}

        # Allows override the proxy on a per-request basis
        # https://requests.readthedocs.io/en/latest/user/advanced/#socks
        # Should also work with `socks5://user:pass@host:port` type syntax.

        if self.proxy_override:
            proxies = {'http': self.proxy_override, 'https': self.proxy_override, 'ftp': self.proxy_override}
        else:
            if self.system_http_proxy:
                proxies['http'] = self.system_http_proxy
            if self.system_https_proxy:
                proxies['https'] = self.system_https_proxy

        max_retries = int(os.getenv("REQUESTS_RETRY_MAX_COUNT", "6"))
        use_curl_cffi = self.curl_cffi_enabled and not url.startswith('file://')
        if use_curl_cffi:
            try:
                from curl_cffi import requests as curl_requests
                session = curl_requests.Session(impersonate=self.curl_cffi_impersonate)
                logger.debug(f"Using curl_cffi impersonation '{self.curl_cffi_impersonate}' for '{url}'")
            except Exception as e:
                logger.warning(f"curl_cffi unavailable for '{url}', falling back to requests: {e}")
                use_curl_cffi = False

        if not use_curl_cffi:
            session = requests.Session()

            # Configure retry adapter for low-level network errors only
            # Retries connection timeouts, read timeouts, connection resets - not HTTP status codes
            # Especially helpful in parallel test execution when servers are slow/overloaded
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            retry_strategy = Retry(
                total=max_retries,
                connect=max_retries,
                read=max_retries,
                status=0,
                backoff_factor=0.5,
                allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
                raise_on_status=False
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        def request_with_retries(method, request_url, data=None):
            request_kwargs = {
                'method': method,
                'data': data,
                'url': request_url,
                'headers': request_headers,
                'timeout': timeout,
                'proxies': proxies,
                'verify': False,
                'allow_redirects': False,
            }

            if not use_curl_cffi:
                return session.request(**request_kwargs)

            for attempt in range(max_retries + 1):
                try:
                    return session.request(**request_kwargs)
                except Exception:
                    if attempt >= max_retries:
                        raise
                    time.sleep(min(0.5 * (2 ** attempt), 5))

            raise RuntimeError("HTTP request retry loop exited unexpectedly")

        if strtobool(os.getenv('ALLOW_FILE_URI', 'false')) and url.startswith('file://'):
            from requests_file import FileAdapter
            session.mount('file://', FileAdapter())

        allow_iana_restricted = strtobool(os.getenv('ALLOW_IANA_RESTRICTED_ADDRESSES', 'false'))

        try:
            # Fresh DNS check at fetch time — catches DNS rebinding regardless of add-time cache.
            # Shared with every other fetch entry point, so the scheme allowlist and the
            # parser-differential rejection (GHSA-rph4-96w6-q594) stay in step here too.
            # Per-redirect-hop re-validation is done separately in the loop below.
            ok, reason = is_fetch_url_allowed(url)
            if not ok:
                raise Exception(reason)

            request_data = request_body.encode('utf-8') if type(request_body) is str else request_body
            r = request_with_retries(request_method, url, data=request_data)

            # Manually follow redirects so each hop's resolved IP can be validated,
            # preventing SSRF via an open redirect on a public host.
            current_url = url
            for _ in range(10):
                if not r.is_redirect:
                    break
                location = r.headers.get('Location', '')
                redirect_url = urljoin(current_url, location)
                if not allow_iana_restricted:
                    if is_url_private_or_parser_confused(redirect_url):
                        raise Exception(f"Redirect blocked: '{redirect_url}' resolves to a private/reserved IP address "
                                        f"or contains a parser-differential payload.")
                current_url = redirect_url
                r = request_with_retries('GET', redirect_url)
            else:
                raise Exception("Too many redirects")

        except Exception as e:
            msg = str(e)
            if proxies and 'SOCKSHTTPSConnectionPool' in msg:
                msg = f"Proxy connection failed? {msg}"
            raise Exception(msg) from e

        self.headers = r.headers
        self.status_code = r.status_code

        if r.status_code == 304:
            self.not_modified = True
            self.http_cache_etag = r.headers.get('ETag') or self.http_cache_etag
            self.http_cache_last_modified = r.headers.get('Last-Modified') or self.http_cache_last_modified
            self.content = None
            self.raw_content = b''
            logger.debug(f"Requests returned 304 Not Modified for '{url}'")
            return

        self.http_cache_etag = r.headers.get('ETag')
        self.http_cache_last_modified = r.headers.get('Last-Modified')

        # If the response did not tell us what encoding format to expect, Then use chardet to override what `requests` thinks.
        # For example - some sites don't tell us it's utf-8, but return utf-8 content
        # This seems to not occur when using webdriver/selenium, it seems to detect the text encoding more reliably.
        # https://github.com/psf/requests/issues/1604 good info about requests encoding detection
        if not is_binary:
            # Don't run this for PDF (and requests identified as binary) takes a _long_ time
            if not r.headers.get('content-type') or not 'charset=' in r.headers.get('content-type'):
                # For XML/RSS feeds, check the XML declaration for encoding attribute
                # This is more reliable than chardet which can misdetect UTF-8 as MacRoman
                content_type = r.headers.get('content-type', '').lower()
                if 'xml' in content_type or 'rss' in content_type:
                    # Look for <?xml version="1.0" encoding="UTF-8"?>
                    xml_encoding_match = re.search(rb'<\?xml[^>]+encoding=["\']([^"\']+)["\']', r.content[:200])
                    if xml_encoding_match:
                        r.encoding = xml_encoding_match.group(1).decode('ascii')
                    else:
                        # Default to UTF-8 for XML if no encoding found
                        r.encoding = 'utf-8'
                else:
                    # No charset in HTTP header - sniff encoding in priority order matching browsers
                    # (WHATWG encoding sniffing algorithm):
                    # 1. BOM - highest confidence, check before anything else
                    # 2. <meta charset> in first 2kb
                    # 3. chardet statistical detection - last resort
                    # See: https://github.com/dgtlmoon/changedetection.io/issues/3952
                    boms = [
                        (b'\xef\xbb\xbf', 'utf-8-sig'),
                        (b'\xff\xfe', 'utf-16-le'),
                        (b'\xfe\xff', 'utf-16-be'),
                    ]
                    bom_encoding = next((enc for bom, enc in boms if r.content.startswith(bom)), None)
                    if bom_encoding:
                        logger.info(f"URL: {url} Using encoding '{bom_encoding}' detected from BOM")
                        r.encoding = bom_encoding
                    else:
                        meta_charset_match = re.search(rb'<meta[^>]+charset\s*=\s*["\']?\s*([^"\'\s;>]+)', r.content[:2000], re.IGNORECASE)
                        if meta_charset_match:
                            encoding = meta_charset_match.group(1).decode('ascii', errors='ignore')
                            logger.info(f"URL: {url} No content-type encoding in HTTP headers - Using encoding '{encoding}' from HTML meta charset tag")
                            r.encoding = encoding
                        else:
                            encoding = chardet.detect(r.content)['encoding']
                            logger.warning(f"URL: {url} No charset in headers or meta tag, guessed encoding as '{encoding}' via chardet")
                            if encoding:
                                r.encoding = encoding

        if not r.content or not len(r.content):
            logger.debug(f"Requests returned empty content for '{url}'")
            if not empty_pages_are_a_change:
                raise EmptyReply(url=url, status_code=r.status_code)
            else:
                logger.debug(f"URL {url} gave zero byte content reply with Status Code {r.status_code}, but empty_pages_are_a_change = True")

        # @todo test this
        # @todo maybe you really want to test zero-byte return pages?
        if r.status_code != 200 and not ignore_status_codes:
            # maybe check with content works?
            raise Non200ErrorCodeReceived(url=url, status_code=r.status_code, page_html=r.text)

        if is_binary:
            # Binary files just return their checksum until we add something smarter
            self.content = hashlib.md5(r.content).hexdigest()
        else:
            self.content = r.text

        self.raw_content = r.content

        # If the content is an image, set it as screenshot for SSIM/visual comparison
        content_type = r.headers.get('content-type', '').lower()
        if 'image/' in content_type:
            self.screenshot = r.content
            logger.debug(f"Image content detected ({content_type}), set as screenshot for comparison")

    async def run(self,
                  fetch_favicon=True,
                  current_include_filters=None,
                  empty_pages_are_a_change=False,
                  ignore_status_codes=False,
                  is_binary=False,
                  request_body=None,
                  request_headers=None,
                  request_method=None,
                  screenshot_format=None,
                  timeout=None,
                  url=None,
                  watch_uuid=None,
                  ):
        """Async wrapper that runs the synchronous requests code in a thread pool"""

        loop = asyncio.get_event_loop()

        # Run the synchronous _run_sync in a thread pool to avoid blocking the event loop
        # Retry logic is handled by requests' HTTPAdapter (see _run_sync for configuration)
        await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            lambda: self._run_sync(
                url=url,
                timeout=timeout,
                request_headers=request_headers,
                request_body=request_body,
                request_method=request_method,
                ignore_status_codes=ignore_status_codes,
                current_include_filters=current_include_filters,
                is_binary=is_binary,
                empty_pages_are_a_change=empty_pages_are_a_change,
                watch_uuid=watch_uuid,
            )
        )

    async def quit(self, watch=None):
        # In case they switched to `requests` fetcher from something else
        # Then the screenshot could be old, in any case, it's not used here.
        # REMOVE_REQUESTS_OLD_SCREENSHOTS - Mainly used for testing
        if strtobool(os.getenv("REMOVE_REQUESTS_OLD_SCREENSHOTS", 'true')):
            screenshot = watch.get_screenshot()
            if screenshot:
                try:
                    os.unlink(screenshot)
                except Exception as e:
                    logger.warning(f"Failed to unlink screenshot: {screenshot} - {e}")


# Plugin registration for built-in fetcher
class RequestsFetcherPlugin:
    """Plugin class that registers the requests fetcher as a built-in plugin."""

    def register_content_fetcher(self):
        """Register the requests fetcher"""
        return ('html_requests', fetcher)


# Create module-level instance for plugin registration
requests_plugin = RequestsFetcherPlugin()
