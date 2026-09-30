"""Buy-signal safety for the restock processor.

Covers the acceptance criteria in KAZE_MONITOR_DESIGN.md: a transition is flagged only
for a confirmed out-of-stock -> in-stock change, the same transition always resolves to
the same ID, and every ambiguous outcome (price-only check, unknown availability, a page
where stock cannot be identified) must produce no buy signal at all.
"""

from types import SimpleNamespace

import pytest

from changedetectionio.processors.exceptions import ProcessorException
from changedetectionio.processors.restock_diff import Restock, build_transition_id
from changedetectionio.processors.restock_diff.processor import perform_site_check

IN_STOCK = {'availability': 'https://schema.org/InStock', 'price': 100.0}
OUT_OF_STOCK = {'availability': 'https://schema.org/OutOfStock', 'price': 100.0}

WATCH_UUID = 'watch-uuid'


class _Watch(dict):
    was_edited = False
    link = 'https://example.com/product'


def run_check(previous_restock, itemprop, configured=None, restock_settings=None):
    """Run one restock check against a stubbed fetcher and return (changed, update_obj)."""
    watch = _Watch(uuid=WATCH_UUID, url='https://example.com/product', tags=[],
                   restock=Restock(previous_restock), previous_md5='old')

    checker = object.__new__(perform_site_check)
    checker.last_raw_content_checksum = None
    checker.fetcher = SimpleNamespace(content='<p>product</p>', headers={}, screenshot=None,
                                      xpath_data=None, instock_data=None, backend_name='',
                                      get_last_status_code=lambda: 200)
    checker.get_raw_document_checksum = lambda: 'new'
    checker.get_restock_settings = lambda _: restock_settings or {
        'follow_price_changes': False, 'in_stock_processing': 'in_stock_only'}
    # Real signature: (itemprop_availability, multiple_prices_found, configured, source)
    checker.extract_product_data = lambda _: (
        dict(itemprop), False,
        configured if configured is not None else {'price': None, 'availability': None},
        'metadata')
    checker.update_last_raw_content_checksum = lambda _: None

    changed, update_obj, _ = checker.run_changedetection(watch)
    return changed, update_obj


def test_out_of_stock_to_in_stock_flags_exactly_one_transition():
    changed, update = run_check({'in_stock': False, 'price': 100.0}, IN_STOCK)
    assert changed is True
    assert update['restock_check_state'] == 'in_stock'
    assert update['restock_transition'] == 'in_stock'


def test_same_transition_always_resolves_to_the_same_id():
    """A retry of one transition must reuse its ID so the consumer can de-duplicate."""
    first = build_transition_id(WATCH_UUID, 1700000000)
    retry = build_transition_id(WATCH_UUID, 1700000000.0)

    assert first == retry == f'{WATCH_UUID}:1700000000'
    # A genuinely later transition is a different event.
    assert build_transition_id(WATCH_UUID, 1700000001) != first


def test_staying_in_stock_does_not_flag_another_transition():
    _, update = run_check({'in_stock': True, 'price': 100.0}, IN_STOCK)
    assert update['restock_transition'] is None


def test_going_out_of_stock_is_not_a_buy_signal():
    _, update = run_check({'in_stock': True, 'price': 100.0}, OUT_OF_STOCK)
    assert update['restock_check_state'] == 'out_of_stock'
    assert update['restock_transition'] is None


def test_price_change_while_out_of_stock_is_not_a_buy_signal():
    _, update = run_check(
        {'in_stock': False, 'price': 100.0},
        {'availability': 'https://schema.org/OutOfStock', 'price': 80.0},
        restock_settings={'follow_price_changes': True, 'in_stock_processing': 'all_changes'})

    assert update['restock']['in_stock'] is False
    assert update['restock_transition'] is None


def test_unknown_availability_is_not_a_buy_signal():
    """A price-only check keeps the last known stock state and must not signal."""
    _, update = run_check(
        {'in_stock': False, 'price': 100.0},
        {'availability': None, 'price': 80.0},
        configured={'price': 80.0, 'availability': None},
        restock_settings={'follow_price_changes': True, 'in_stock_processing': 'all_changes'})

    assert update['restock_check_state'] == 'unknown'
    assert update['restock_transition'] is None


def test_page_without_identifiable_stock_raises_instead_of_signalling():
    """Blocked/challenge pages carry no stock data - fail the check rather than signal."""
    with pytest.raises(ProcessorException):
        run_check({'in_stock': False, 'price': 100.0}, {'availability': None, 'price': None})


def test_transition_id_is_exposed_to_the_api_for_polling():
    """Account Vault re-reads the watch over the API, so the ID must survive serialisation."""
    from changedetectionio.api import strip_internal_api_fields

    exposed = strip_internal_api_fields({
        'restock_transition_id': f'{WATCH_UUID}:1700000000',
        'restock_check_state': 'in_stock',
        '__internal': 'hidden',
    })

    assert exposed['restock_transition_id'] == f'{WATCH_UUID}:1700000000'
    assert exposed['restock_check_state'] == 'in_stock'
    assert '__internal' not in exposed


def test_transition_id_is_offered_as_a_notification_token():
    from changedetectionio.processors.restock_diff import Watch as RestockWatch

    watch = RestockWatch(**{
        '__datastore': SimpleNamespace(),
        '__datastore_path': '/tmp',
        'default': {'url': 'https://example.com/product',
                    'restock_transition_id': f'{WATCH_UUID}:1700000000'},
    })

    assert watch.extra_notification_token_values()['transition_id'] == f'{WATCH_UUID}:1700000000'
    assert 'transition_id' in dict(watch.extra_notification_token_placeholder_info())
