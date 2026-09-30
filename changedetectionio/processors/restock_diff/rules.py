"""Per-watch extraction rules shared by normal checks and diagnostics."""
import re

from bs4 import BeautifulSoup


def _labels(value):
    return [part.strip().casefold() for part in re.split(r'[\n,]+', (value or '')[:1000]) if part.strip()][:50]


def availability_state(value):
    value = re.sub(r'[\s_-]+', '', str(value or '').casefold())
    if any(word in value for word in ('outofstock', 'soldout', 'discontinued', 'preorder')):
        return False
    if any(word in value for word in ('instock', 'instoreonly', 'limitedavailability', 'onlineonly', 'presale')):
        return True
    return None


def extract_configured(html, settings):
    result = {'availability': None, 'price': None, 'in_stock': None, 'evidence': [], 'error': None}
    selectors = (settings.get('availability_selector'), settings.get('price_selector'))
    if not any(selectors):
        return result
    soup = BeautifulSoup((html or '')[:2_000_000], 'html.parser')
    for kind, selector in zip(('availability', 'price'), selectors):
        if not selector:
            continue
        if len(selector) > 300:
            result['error'] = f'{kind} selector is too long'
            continue
        try:
            nodes = soup.select(selector, limit=20)
        except Exception:
            result['error'] = f'Invalid {kind} CSS selector'
            continue
        texts = [node.get_text(' ', strip=True)[:300] for node in nodes]
        texts = [s for s in texts if s]
        result['evidence'].append({'source': 'CSS selector', 'field': kind,
                                   'selector': selector, 'matches': texts[:10], 'count': len(nodes)})
        if kind == 'availability':
            positive = _labels(settings.get('in_stock_labels'))
            negative = _labels(settings.get('out_of_stock_labels'))
            if not positive and not negative:
                result['error'] = 'Set in-stock or out-of-stock labels for the availability selector'
                continue
            # Prefer the most specific label within each text: "out of stock"
            # must not also count as a match for "in stock".
            matches = []
            for text in texts:
                candidates = [(len(label), True) for label in positive if label in text.casefold()]
                candidates += [(len(label), False) for label in negative if label in text.casefold()]
                if candidates:
                    longest = max(length for length, _ in candidates)
                    matches.extend(state for length, state in candidates if length == longest)
            found_positive = True in matches
            found_negative = False in matches
            if found_positive != found_negative:
                result['in_stock'] = found_positive
                result['availability'] = 'InStock' if found_positive else 'OutOfStock'
            elif found_positive:
                result['error'] = 'Conflicting stock labels matched'
            else:
                result['error'] = 'No stock label matched'
        else:
            prices = set()
            for value in texts:
                for number in re.findall(r'(?<!\d)\d[\d,]*(?:\.\d{1,2})?(?!\d)', value):
                    try:
                        prices.add(float(number.replace(',', '')))
                    except ValueError:
                        pass
            if len(prices) == 1:
                result['price'] = prices.pop()
            elif len(prices) > 1:
                result['error'] = 'Multiple prices matched the price selector'
    return result
