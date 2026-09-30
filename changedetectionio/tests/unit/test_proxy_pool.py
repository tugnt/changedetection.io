import json
import threading
import pytest

from changedetectionio.store import ChangeDetectionStore
from changedetectionio.content_fetchers.playwright import fetcher as PlaywrightFetcher


def test_proxy_pool_rotates_without_changing_static_proxy(tmp_path):
    (tmp_path / 'proxies.json').write_text(json.dumps({
        'pool': {
            'label': 'Pool',
            'urls': ['http://proxy-one:8000', 'http://proxy-two:8000'],
        },
        'static': {'label': 'Static', 'url': 'http://proxy-static:8000'},
    }), encoding='utf-8')
    store = ChangeDetectionStore.__new__(ChangeDetectionStore)
    store.datastore_path = str(tmp_path)
    store._ChangeDetectionStore__data = {
        'settings': {'application': {}, 'requests': {'extra_proxies': None}}
    }
    store._proxy_rotation_lock = threading.Lock()
    store._proxy_rotation_positions = {}

    assert store.proxy_list['pool']['url'] == 'http://proxy-one:8000'
    assert store.get_proxy_url('pool') == 'http://proxy-one:8000'
    assert store.get_proxy_url('pool') == 'http://proxy-two:8000'
    assert store.get_proxy_url('pool') == 'http://proxy-one:8000'
    assert store.get_proxy_url('static') == 'http://proxy-static:8000'


def test_empty_proxy_pool_fails_instead_of_fetching_directly(tmp_path):
    (tmp_path / 'proxies.json').write_text(
        '{"pool": {"label": "Pool", "urls": []}}', encoding='utf-8'
    )
    store = ChangeDetectionStore.__new__(ChangeDetectionStore)
    store.datastore_path = str(tmp_path)
    store._ChangeDetectionStore__data = {
        'settings': {'application': {}, 'requests': {'extra_proxies': None}}
    }
    store._proxy_rotation_lock = threading.Lock()
    store._proxy_rotation_positions = {}

    with pytest.raises(ValueError, match='has no valid URLs'):
        store.get_proxy_url('pool')


def test_authenticated_proxy_is_split_for_playwright():
    fetcher = PlaywrightFetcher(proxy_override='http://user:p%40ss@proxy.example:10000')
    assert fetcher.proxy == {
        'server': 'http://proxy.example:10000',
        'username': 'user',
        'password': 'p@ss',
    }
