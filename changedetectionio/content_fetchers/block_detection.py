"""Recognize access-control interstitials before they enter watch history.

Only strong, provider-specific signatures are used here. Ordinary pages can
mention CAPTCHA or access denied in their product text.
"""

import re
from html import unescape


def detect_block_page(content, headers=None):
    """Return the blocking provider, or None for normal content."""
    if not isinstance(content, str) or not content:
        return None

    headers = headers or {}
    content_type = str(headers.get('content-type', '')).lower()
    if content_type and 'html' not in content_type:
        return None

    # Challenge pages are small. A large product page can legitimately embed
    # challenge-related scripts without being blocked.
    if len(content) > 200_000:
        return None

    html = unescape(content).lower()
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
    title = re.sub(r'\s+', ' ', title_match.group(1)).strip() if title_match else ''

    if 'errors.edgesuite.net/' in html and title == 'access denied':
        return 'Akamai'
    if ('/akam/13/' in html or 'akamai bot manager' in html) and title in (
            'access denied', 'please wait', 'request rejected'):
        return 'Akamai'
    if ('cf-chl-' in html or '/cdn-cgi/challenge-platform/' in html) and title in (
            'just a moment...', 'attention required! | cloudflare', 'checking your browser'):
        return 'Cloudflare'
    if ('captcha-delivery.com/' in html or 'datadome.co/' in html) and (
            'datadome' in html or 'captcha' in title):
        return 'DataDome'
    if ('_pxcaptcha' in html or 'px-captcha' in html) and (
            'captcha' in title or 'access denied' in title):
        return 'PerimeterX'
    return None
