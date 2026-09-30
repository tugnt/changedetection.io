"""Read Sony Store product availability from its visible product panel."""

import re
from html import unescape
from urllib.parse import urlparse


_PRICE = re.compile(
    r'<[^>]*class=["\'][^"\']*\bs5-sonystoreBlock__buyAtStorePrice\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
    re.IGNORECASE | re.DOTALL,
)
_STATUS = re.compile(
    r'<[^>]*class=["\'][^"\']*\bs5-shippingLabel\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r'<[^>]+>')
_AVAILABILITY = {
    '入荷待ち': 'https://schema.org/OutOfStock',
    '在庫なし': 'https://schema.org/OutOfStock',
    '販売終了': 'https://schema.org/Discontinued',
    '在庫あり': 'https://schema.org/InStock',
    '予約受付中': 'https://schema.org/PreSale',
}


def extract_sony_store_product(html, url):
    """Return price/availability only when one unambiguous product value exists."""
    if not isinstance(html, str) or not html:
        return {}
    parsed = urlparse(url)
    if parsed.hostname != 'pur.store.sony.jp' or not parsed.path.endswith('_product/'):
        return {}

    statuses = {unescape(_TAGS.sub('', match)).strip() for match in _STATUS.findall(html)}
    prices = {unescape(_TAGS.sub('', match)).strip() for match in _PRICE.findall(html)}
    if len(statuses) != 1 or len(prices) > 1:
        return {}
    status = next(iter(statuses))
    if status not in _AVAILABILITY:
        return {}
    result = {'availability': _AVAILABILITY[status]}

    if len(prices) == 1:
        raw_price = next(iter(prices)).replace(',', '').replace('，', '')
        if re.fullmatch(r'[0-9]+', raw_price):
            result['price'] = float(raw_price)
            result['currency'] = 'JPY'

    return result
