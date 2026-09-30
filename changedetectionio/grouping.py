"""Resolve the one direct group assigned to a watch."""

import fnmatch


def group_matches_url(group, url):
    pattern = (group.get('url_match_pattern') or '').strip()
    if not pattern or not url:
        return False
    pattern = pattern.lower()
    url = url.lower()
    if any(char in pattern for char in ('*', '?', '[')):
        return fnmatch.fnmatch(url, pattern)
    return pattern in url


def direct_group_for_watch(watch, groups):
    """Return (UUID, group), preferring the first stored UUID over URL rules."""
    if not watch:
        return None
    assigned = watch.get('tags') or []
    if isinstance(assigned, list):
        for group_uuid in assigned:
            if group_uuid in groups:
                return group_uuid, groups[group_uuid]
    url = watch.get('url') or ''
    for group_uuid, group in groups.items():
        if group_matches_url(group, url):
            return group_uuid, group
    return None
