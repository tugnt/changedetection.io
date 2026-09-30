"""Limit repeated checks after a website rejects access."""

import os


def access_block_delay(block_count):
    """Seconds to wait after consecutive access blocks, capped at one hour."""
    try:
        count = max(0, int(block_count or 0))
    except (TypeError, ValueError):
        return 0
    if not count:
        return 0
    base = max(1, int(os.getenv('ACCESS_BLOCK_RECHECK_BASE_SECONDS', '120')))
    maximum = max(base, int(os.getenv('ACCESS_BLOCK_RECHECK_MAX_SECONDS', '3600')))
    return min(maximum, base * (2 ** min(count - 1, 16)))


def watch_access_block_delay(watch):
    """Include watches that already had a 403 before block counters existed."""
    count = watch.get('consecutive_access_blocks', 0)
    if not count:
        error = str(watch.get('last_error') or '')
        if error.startswith(('Error - 403', 'Error - Request returned a HTTP error code 429',
                             'Blocked by ')):
            count = 1
    return access_block_delay(count)
