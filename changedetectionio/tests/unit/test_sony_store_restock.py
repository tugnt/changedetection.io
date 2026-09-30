from changedetectionio.processors.restock_diff.sony_store import extract_sony_store_product


URL = 'https://pur.store.sony.jp/ps5/products/ps5/CFI-7100B01_product/'


def test_sony_store_waiting_for_stock_and_yen_price():
    html = '''<div class="s5-sonystoreBlock">
      <span class="s5-sonystoreBlock__buyAtStorePrice">137,980</span>
      <a class="s5-shippingLabel s5-shippingLabel--2">入荷待ち</a>
    </div>'''
    assert extract_sony_store_product(html, URL) == {
        'availability': 'https://schema.org/OutOfStock',
        'price': 137980.0,
        'currency': 'JPY',
    }


def test_sony_store_in_stock_label():
    html = '<span class="s5-sonystoreBlock__buyAtStorePrice">137,980</span><a class="s5-shippingLabel">在庫あり</a>'
    assert extract_sony_store_product(html, URL)['availability'] == 'https://schema.org/InStock'


def test_sony_store_ignores_conflicting_status_and_other_domains():
    html = ('<a class="s5-shippingLabel">入荷待ち</a>'
            '<a class="s5-shippingLabel">在庫あり</a>')
    assert extract_sony_store_product(html, URL) == {}
    assert extract_sony_store_product(html, 'https://example.com/item') == {}
