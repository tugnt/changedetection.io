from changedetectionio.recheck_backoff import access_block_delay, watch_access_block_delay


def test_access_blocks_back_off_and_cap(monkeypatch):
    monkeypatch.delenv('ACCESS_BLOCK_RECHECK_BASE_SECONDS', raising=False)
    monkeypatch.delenv('ACCESS_BLOCK_RECHECK_MAX_SECONDS', raising=False)
    assert access_block_delay(0) == 0
    assert access_block_delay(1) == 120
    assert access_block_delay(2) == 240
    assert access_block_delay(3) == 480
    assert access_block_delay(20) == 3600


def test_access_block_delay_is_configurable(monkeypatch):
    monkeypatch.setenv('ACCESS_BLOCK_RECHECK_BASE_SECONDS', '30')
    monkeypatch.setenv('ACCESS_BLOCK_RECHECK_MAX_SECONDS', '90')
    assert access_block_delay(1) == 30
    assert access_block_delay(2) == 60
    assert access_block_delay(3) == 90


def test_existing_watch_with_403_backs_off_after_restart(monkeypatch):
    monkeypatch.delenv('ACCESS_BLOCK_RECHECK_BASE_SECONDS', raising=False)
    assert watch_access_block_delay({'last_error': 'Error - 403 (Access denied) received'}) == 120
    assert watch_access_block_delay({'last_error': False, 'consecutive_access_blocks': 0}) == 0
