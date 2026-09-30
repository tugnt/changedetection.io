from changedetectionio.processors.restock_diff.rules import extract_configured, availability_state


def test_japanese_stock_and_price_rules():
    html = '<span class="stock">入荷待ち</span><b class="price">¥137,980</b>'
    result = extract_configured(html, {
        'availability_selector': '.stock', 'out_of_stock_labels': '入荷待ち',
        'in_stock_labels': '在庫あり', 'price_selector': '.price'})
    assert result['in_stock'] is False
    assert result['price'] == 137980.0
    assert result['evidence'][0]['matches'] == ['入荷待ち']


def test_overlapping_labels_and_conflict():
    settings = {'availability_selector': '.stock', 'in_stock_labels': 'in stock',
                'out_of_stock_labels': 'out of stock'}
    assert extract_configured('<div class="stock">out of stock</div>', settings)['in_stock'] is False
    result = extract_configured('<div class="stock">in stock</div><div class="stock">out of stock</div>', settings)
    assert result['in_stock'] is None
    assert result['error'] == 'Conflicting stock labels matched'


def test_missing_or_invalid_selector_is_unknown():
    settings = {'availability_selector': '???', 'in_stock_labels': 'yes'}
    result = extract_configured('<div>yes</div>', settings)
    assert result['in_stock'] is None
    assert result['error'] == 'Invalid availability CSS selector'


def test_unknown_metadata_does_not_mean_out_of_stock():
    assert availability_state('https://schema.org/OutOfStock') is False
    assert availability_state('https://schema.org/InStock') is True
    assert availability_state('out of stock') is False
    assert availability_state('TemporarilyUnavailable') is None


def test_price_only_check_preserves_last_known_stock_and_does_not_notify():
    from types import SimpleNamespace
    from changedetectionio.processors.restock_diff.processor import perform_site_check
    from changedetectionio.processors.restock_diff import Restock

    class Watch(dict):
        was_edited = False
        link = 'https://example.com/product'

    watch = Watch(uuid='watch', url='https://example.com/product', tags=[],
                  restock=Restock({'in_stock': False, 'price': 100.0}), previous_md5='old')
    checker = object.__new__(perform_site_check)
    checker.last_raw_content_checksum = None
    checker.fetcher = SimpleNamespace(content='<p>Price: 100</p>', headers={}, screenshot=None,
                                      xpath_data=None, instock_data=None, backend_name='',
                                      get_last_status_code=lambda: 200)
    checker.get_raw_document_checksum = lambda: 'new'
    checker.get_restock_settings = lambda _: {'follow_price_changes': True, 'in_stock_processing': 'all_changes'}
    checker.extract_product_data = lambda _: ({'price': 100.0}, False, {'availability': None, 'price': None}, 'metadata')
    saved_checksums = []
    checker.update_last_raw_content_checksum = saved_checksums.append

    changed, update, _ = checker.run_changedetection(watch)
    assert changed is False
    assert update['restock_check_state'] == 'unknown'
    assert update['restock']['in_stock'] is False
    assert saved_checksums == ['new']


def test_diagnostic_endpoint_does_not_mutate_watch(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from flask import Flask
    from werkzeug.routing import BaseConverter
    from changedetectionio.blueprint.ui.edit import construct_blueprint
    import changedetectionio.content_fetchers as fetchers
    import changedetectionio.processors.restock_diff.processor as processor_module

    class UUIDConverter(BaseConverter):
        regex = '[^/]+'

    watch = {'processor': 'restock_diff', 'restock': {'in_stock': False, 'price': 100},
             'history': {'123': 'snapshot'}, 'last_checked': 123}
    before = deepcopy(watch)
    datastore = SimpleNamespace(data={'watching': {'id': watch}, 'settings': {'application': {}}},
                                proxy_list=None, get_preferred_proxy_for_watch=lambda uuid: None)

    class FakeCheck:
        def __init__(self, datastore, watch_uuid):
            class FakeWatch(dict):
                link = 'https://example.com/product'
            self.watch = FakeWatch(tags=[])
            self.fetcher = SimpleNamespace(backend_name='html_requests', instock_data=None,
                                           content='<span class="stock">入荷待ち</span>',
                                           get_last_status_code=lambda: 200)

        def get_restock_settings(self, watch):
            return {}

        async def call_browser(self, diagnostic=False):
            assert diagnostic is True

        def extract_product_data(self, watch):
            from changedetectionio.processors.restock_diff.rules import extract_configured
            selected = extract_configured(self.fetcher.content, self.diagnostic_settings)
            return ({'availability': selected['availability'], 'price': 100.0}, False, selected, 'CSS selector')

    monkeypatch.setattr(processor_module, 'perform_site_check', FakeCheck)
    monkeypatch.setattr(fetchers, 'resolve_content_fetcher', lambda watch, datastore: (None, 'html_requests', None))
    app = Flask(__name__)
    app.url_map.converters['uuid_str'] = UUIDConverter
    app.config.update(DATASTORE=datastore, LOGIN_DISABLED=True)
    app.register_blueprint(construct_blueprint(datastore, None, None))
    response = app.test_client().post('/edit/id/test-restock', data={
        'processor_config_restock_diff-availability_selector': '.stock',
        'processor_config_restock_diff-out_of_stock_labels': '入荷待ち'})
    assert response.status_code == 200
    assert response.json['state'] == 'out_of_stock'
    assert response.json['evidence'][0]['matches'] == ['入荷待ち']
    assert watch == before

    from changedetectionio.content_fetchers.exceptions import BlockPageReceived

    async def blocked(self, diagnostic=False):
        raise BlockPageReceived(provider='Akamai', status_code=403)

    monkeypatch.setattr(FakeCheck, 'call_browser', blocked)
    blocked_response = app.test_client().post('/edit/id/test-restock')
    assert blocked_response.json['state'] == 'blocked'
    assert blocked_response.json['http_status'] == 403
    assert watch == before


def test_restock_edit_panel_renders_test_action():
    from pathlib import Path
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader
    from changedetectionio.processors.restock_diff.forms import RestockSettingsForm, processor_settings_form

    source = processor_settings_form.extra_form_content(SimpleNamespace())
    templates_dir = Path(__file__).resolve().parents[2] / 'templates'
    html = Environment(loader=FileSystemLoader(str(templates_dir))).from_string(source).render(
        form=SimpleNamespace(processor_config_restock_diff=RestockSettingsForm()),
        watch={'restock': {}}, restock_test_url='/edit/id/test-restock')
    assert 'test-restock-watch' in html
    assert 'fetch("/edit/id/test-restock"' in html
    assert "].join('\\n')" in html
