from changedetectionio.content_fetchers.block_detection import detect_block_page
from changedetectionio.blueprint.add_watch_ui import snapshot_block_error
from changedetectionio.blueprint.add_watch_ui import preview_proxy_settings
from types import SimpleNamespace
import pytest


def test_akamai_encoded_access_denied_response():
    html = '''<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>
    You don't have permission to access this page.
    <P>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18.123</P>
    </BODY></HTML>'''
    assert detect_block_page(html, {'content-type': 'text/html'}) == 'Akamai'


def test_browser_challenge_responses():
    assert detect_block_page(
        '<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
    ) == 'Cloudflare'
    assert detect_block_page(
        '<title>Are you human?</title><script src="https://geo.captcha-delivery.com/captcha/?datadome=x"></script>'
    ) == 'DataDome'


def test_product_page_mentioning_captcha_is_not_blocked():
    html = '<title>Security software</title><h1>CAPTCHA protection</h1><p>Access Denied is an example message.</p>'
    assert detect_block_page(html, {'content-type': 'text/html'}) is None
    assert detect_block_page('<title>Access Denied</title>', {'content-type': 'text/plain'}) is None


def test_add_watch_preview_refuses_akamai_response():
    html = '<title>Access Denied</title><p>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18</p>'
    assert 'Akamai' in snapshot_block_error(html, 403)
    assert 'preview was not saved' in snapshot_block_error(html, 200)
    assert snapshot_block_error('<title>Product</title><p>In stock</p>', 200) is None


def test_add_watch_preview_uses_selected_proxy():
    datastore = SimpleNamespace(
        proxy_list={
            'pool': {'label': 'Pool', 'url': 'http://user:p%40ss@proxy.example:8000'},
            'no-proxy': {'label': 'No proxy', 'url': ''},
        },
        data={'settings': {'requests': {'proxy': 'pool'}}},
        get_proxy_url=lambda key: 'http://user:p%40ss@proxy.example:8000',
    )
    expected = {'server': 'http://proxy.example:8000', 'username': 'user', 'password': 'p@ss'}
    assert preview_proxy_settings(datastore, '') == expected
    assert preview_proxy_settings(datastore, 'pool') == expected
    assert preview_proxy_settings(datastore, 'no-proxy') is None
    with pytest.raises(ValueError, match='Invalid proxy'):
        preview_proxy_settings(datastore, 'missing')
