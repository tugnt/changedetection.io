"""Two-level watch groups and their notification destination."""

from queue import Queue
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from pathlib import Path
import time

from flask import url_for

from changedetectionio.blueprint.watchlist import filters
from changedetectionio.notification_service import NotificationService, _check_cascading_vars


def _api_headers(datastore):
    return {'x-api-key': datastore.data['settings']['application']['api_access_token']}


def test_child_groups_scope_watches_and_route_one_notification_channel(
        client, live_server, measure_memory_usage, datastore_path):
    client.environ_base['HTTP_ACCEPT_LANGUAGE'] = 'en'
    datastore = live_server.app.config['DATASTORE']

    client.post(url_for('tags.form_tag_add'), data={'name': 'camera'})
    camera = datastore.tag_uuid_for_title('camera')
    assert camera
    client.post(url_for('tags.form_tag_add'), data={'name': 'film', 'parent_uuid': camera})
    client.post(url_for('tags.form_tag_add'), data={'name': 'digital-camera', 'parent_uuid': camera})
    film = datastore.tag_uuid_for_title('film')
    digital = datastore.tag_uuid_for_title('digital-camera')
    assert datastore.data['settings']['application']['tags'][film]['parent_uuid'] == camera

    direct = datastore.add_watch('https://example.com/camera', tag='camera', extras={'paused': True})
    film_watch = datastore.add_watch('https://example.com/film', tag='film,digital-camera', extras={'paused': True})
    digital_watch = datastore.add_watch('https://example.com/digital', tag='digital-camera', extras={'paused': True})
    assert datastore.data['watching'][film_watch]['tags'] == [film]
    assert set(filters.matching_watch_uuids(datastore, {'tag': camera})) == {direct, film_watch, digital_watch}
    assert filters.matching_watch_uuids(datastore, {'tag': film}) == [film_watch]
    assert filters.matching_watch_uuids(datastore, {'tag': digital}) == [digital_watch]

    group_page = client.get(url_for('tags.tags_overview_page'))
    assert b'film' in group_page.data and b'digital-camera' in group_page.data

    groups = datastore.data['settings']['application']['tags']
    groups[camera]['notification_urls'] = ['mailto://parent@example.com']
    groups[film]['notification_urls'] = ['mailto://film@example.com']
    datastore.data['settings']['application']['notification_urls'] = ['mailto://global@example.com']
    watch = datastore.data['watching'][film_watch]
    assert _check_cascading_vars(datastore, 'notification_urls', watch) == ['mailto://film@example.com']
    groups[film]['notification_urls'] = []
    assert _check_cascading_vars(datastore, 'notification_urls', watch) == ['mailto://parent@example.com']
    watch['notification_urls'] = ['mailto://watch@example.com']
    assert _check_cascading_vars(datastore, 'notification_urls', watch) == ['mailto://watch@example.com']
    groups[camera]['notification_muted'] = True
    assert _check_cascading_vars(datastore, 'notification_urls', watch) is None
    groups[camera]['notification_muted'] = False
    groups[camera]['include_filters'] = ['#parent-only']
    groups[film]['include_filters'] = ['#film-only']
    assert camera not in datastore.get_all_tags_for_watch(film_watch)
    from changedetectionio.processors.text_json_diff.processor import FilterConfig
    assert FilterConfig(watch, datastore).include_filters == ['#film-only']


def test_existing_multiple_groups_keep_data_but_use_first_group(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    first = datastore.add_tag('first')
    second = datastore.add_tag('second')
    uuid = datastore.add_watch('https://example.com/old-watch', tag='first', extras={'paused': True})
    watch = datastore.data['watching'][uuid]
    watch['tags'] = [first, second]
    assert watch['tags'] == [first, second]
    assert list(datastore.get_all_tags_for_watch(uuid)) == [first]
    assert filters.matching_watch_uuids(datastore, {'tag': second}) == []


def test_group_cannot_have_a_grandchild_and_parent_delete_keeps_children(
        client, live_server, measure_memory_usage, datastore_path):
    client.environ_base['HTTP_ACCEPT_LANGUAGE'] = 'en'
    datastore = live_server.app.config['DATASTORE']
    client.post(url_for('tags.form_tag_add'), data={'name': 'camera'})
    camera = datastore.tag_uuid_for_title('camera')
    client.post(url_for('tags.form_tag_add'), data={'name': 'film', 'parent_uuid': camera})
    film = datastore.tag_uuid_for_title('film')
    client.post(url_for('tags.form_tag_add'), data={'name': 'instax', 'parent_uuid': film})
    assert datastore.tag_uuid_for_title('instax') is None

    client.post(url_for('tags.delete', uuid=camera))
    assert film in datastore.data['settings']['application']['tags']
    assert datastore.data['settings']['application']['tags'][film]['parent_uuid'] == ''


def test_bulk_reassignment_moves_watch_between_child_groups(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    groups = datastore.data['settings']['application']['tags']
    groups[film]['notification_urls'] = ['mailto://film@example.com']
    groups[digital]['notification_urls'] = ['mailto://digital@example.com']
    watch_uuid = datastore.add_watch('https://example.com/camera-item', tag='film', extras={'paused': True})

    response = client.post(url_for('ui.form_watch_list_checkbox_operations'), data={
        'op': 'assign-tag', 'op_extradata': 'digital-camera', 'uuids': watch_uuid,
    })

    assert response.status_code == 302
    watch = datastore.data['watching'][watch_uuid]
    assert watch['tags'] == [digital]
    assert list(datastore.get_group_path_for_watch(watch_uuid)) == [digital, camera]
    assert filters.matching_watch_uuids(datastore, {'tag': film}) == []
    assert filters.matching_watch_uuids(datastore, {'tag': digital}) == [watch_uuid]
    assert _check_cascading_vars(datastore, 'notification_urls', watch) == ['mailto://digital@example.com']


def test_parent_api_watch_list_and_search_include_children(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    parent_watch = datastore.add_watch('https://example.com/camera', tag='camera',
                                       extras={'title': 'monitor parent', 'paused': True})
    film_watch = datastore.add_watch('https://example.com/film', tag='film',
                                     extras={'title': 'monitor film', 'paused': True})
    digital_watch = datastore.add_watch('https://example.com/digital', tag='digital-camera',
                                        extras={'title': 'monitor digital', 'paused': True})
    datastore.add_watch('https://example.com/other', extras={'title': 'monitor other', 'paused': True})
    headers = _api_headers(datastore)

    for tag in ('camera', camera):
        response = client.get(url_for('createwatch'), query_string={'tag': tag}, headers=headers)
        assert response.status_code == 200
        assert set(response.json) == {parent_watch, film_watch, digital_watch}
        assert response.json[film_watch]['tags'] == [film]
        assert response.json[digital_watch]['tags'] == [digital]

    response = client.get(url_for('search'), query_string={
        'q': 'monitor', 'partial': 'true', 'tag': 'camera',
    }, headers=headers)
    assert response.status_code == 200
    assert set(response.json) == {parent_watch, film_watch, digital_watch}
    response = client.get(url_for('search'), query_string={
        'q': 'monitor', 'partial': 'true', 'tag': 'film',
    }, headers=headers)
    assert response.status_code == 200
    assert set(response.json) == {film_watch}
    response = client.get(url_for('search'), query_string={
        'q': 'monitor', 'partial': 'true', 'tag': 'missing-group',
    }, headers=headers)
    assert response.status_code == 200
    assert response.json == {}


def test_filter_and_step_failure_alerts_use_one_child_channel_and_respect_mute(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    groups = datastore.data['settings']['application']['tags']
    groups[camera]['notification_urls'] = ['mailto://parent@example.com']
    groups[film]['notification_urls'] = ['mailto://film@example.com']
    groups[camera]['include_filters'] = ['#parent-only']
    groups[film]['include_filters'] = ['#film-only']
    datastore.data['settings']['application']['notification_urls'] = ['mailto://global@example.com']
    watch_uuid = datastore.add_watch('https://example.com/film', tag='film', extras={
        'paused': True,
    })
    notification_q = Queue()
    service = NotificationService(datastore=datastore, notification_q=notification_q)

    service.send_filter_failure_notification(watch_uuid)
    service.send_step_failure_notification(watch_uuid, step_n=1)
    assert notification_q.qsize() == 2
    notifications = [notification_q.get_nowait() for _ in range(2)]
    assert '#film-only' in notifications[0]['notification_body']
    assert '#parent-only' not in notifications[0]['notification_body']
    assert [notification['notification_urls'] for notification in notifications] == [
        ['mailto://film@example.com'], ['mailto://film@example.com'],
    ]

    for muted_group in (film, camera):
        groups[muted_group]['notification_muted'] = True
        service.send_filter_failure_notification(watch_uuid)
        service.send_step_failure_notification(watch_uuid, step_n=1)
        assert notification_q.empty()
        groups[muted_group]['notification_muted'] = False

    watch = datastore.data['watching'][watch_uuid]
    watch['notification_urls'] = ['mailto://watch@example.com']
    watch['notification_muted'] = True
    service.send_filter_failure_notification(watch_uuid)
    service.send_step_failure_notification(watch_uuid, step_n=1)
    assert notification_q.empty()


def test_api_import_requires_one_existing_group_and_explicit_uuid_takes_precedence(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    headers = {**_api_headers(datastore), 'content-type': 'text/plain'}

    for invalid_ids in (f'{film},{digital}', 'not-an-existing-group'):
        response = client.post(url_for('import'), query_string={
            'tag_uuids': invalid_ids, 'paused': 'true',
        }, data='https://example.com/rejected', headers=headers)
        assert response.status_code == 400
        assert datastore.data['watching'] == {}

    response = client.post(url_for('import'), query_string={
        'tag': 'film', 'tag_uuids': digital, 'paused': 'true',
    }, data='https://example.com/imported', headers=headers)
    assert response.status_code == 200
    assert len(response.json) == 1
    watch = datastore.data['watching'][response.json[0]]
    assert watch['tags'] == [digital]
    assert list(datastore.get_group_path_for_watch(watch['uuid'])) == [digital, camera]


def test_legacy_extra_groups_do_not_lend_ai_or_restock_settings(
        client, live_server, measure_memory_usage, datastore_path):
    from changedetectionio.llm.evaluator import llm_enabled_for_watch, resolve_llm_field
    from changedetectionio.processors.restock_diff.processor import perform_site_check

    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    groups = datastore.data['settings']['application']['tags']
    for group_uuid, name, limit in ((camera, 'parent', 33), (film, 'film', 11), (digital, 'digital', 22)):
        group = groups[group_uuid]
        group['llm_backend_profile'] = True
        group['llm_intent'] = f'{name} intent'
        group['overrides_watch'] = True
        group['processor_config_restock_diff'] = {'price_change_min': limit}

    watch_uuid = datastore.add_watch('https://example.com/legacy', tag='film', extras={
        'processor': 'restock_diff', 'paused': True,
    })
    watch = datastore.data['watching'][watch_uuid]
    watch['tags'] = [film, digital]  # Existing data stays intact until the watch is edited.

    assert llm_enabled_for_watch(watch, datastore) == (True, 'film')
    assert resolve_llm_field(watch, datastore, 'llm_intent') == ('film intent', 'film')
    processor = perform_site_check(datastore=datastore, watch_uuid=watch_uuid)
    assert processor.get_restock_settings(watch) == {'price_change_min': 11}

    groups[film]['llm_backend_profile'] = False
    groups[film]['overrides_watch'] = False
    assert llm_enabled_for_watch(watch, datastore) == (False, 'film')
    assert resolve_llm_field(watch, datastore, 'llm_intent') == ('', '')
    assert processor.get_restock_settings(watch) == {
        'follow_price_changes': True, 'in_stock_processing': 'in_stock_only',
    }


def test_api_import_tags_field_accepts_empty_or_one_existing_group(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    headers = {**_api_headers(datastore), 'content-type': 'text/plain'}

    for value in (f'{film},{digital}', f'["{film}", "{digital}"]', 'not-an-existing-group'):
        response = client.post(url_for('import'), query_string={
            'tags': value, 'paused': 'true',
        }, data='https://example.com/rejected', headers=headers)
        assert response.status_code == 400
        assert datastore.data['watching'] == {}

    empty = client.post(url_for('import'), query_string={
        'tags': '[]', 'paused': 'true',
    }, data='https://example.com/ungrouped', headers=headers)
    assert empty.status_code == 200
    assert datastore.data['watching'][empty.json[0]]['tags'] == []

    grouped = client.post(url_for('import'), query_string={
        'tags': film, 'paused': 'true',
    }, data='https://example.com/grouped', headers=headers)
    assert grouped.status_code == 200
    assert datastore.data['watching'][grouped.json[0]]['tags'] == [film]


def test_unlink_parent_clears_manual_assignments_in_its_branch_only(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    audio = datastore.add_tag('audio')
    speakers = datastore.add_tag('speakers', parent_uuid=audio)
    camera_watch = datastore.add_watch('https://example.com/camera', tag='camera', extras={'paused': True})
    film_watch = datastore.add_watch('https://example.com/film', tag='film', extras={'paused': True})
    digital_watch = datastore.add_watch('https://example.com/digital', tag='digital-camera', extras={'paused': True})
    audio_watch = datastore.add_watch('https://example.com/audio', tag='audio', extras={'paused': True})
    speaker_watch = datastore.add_watch('https://example.com/speakers', tag='speakers', extras={'paused': True})
    legacy_watch = datastore.add_watch('https://example.com/legacy-film', tag='film', extras={'paused': True})
    datastore.data['watching'][legacy_watch]['tags'] = [film, speakers]

    threads = []

    def record_thread(*args, **kwargs):
        thread = Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    with patch('changedetectionio.blueprint.tags.threading.Thread', new=record_thread):
        response = client.post(url_for('tags.unlink', uuid=camera))
    assert response.status_code == 302
    assert len(threads) == 1
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()

    for watch_uuid in (camera_watch, film_watch, digital_watch, legacy_watch):
        assert datastore.data['watching'][watch_uuid]['tags'] == []
    assert datastore.data['watching'][audio_watch]['tags'] == [audio]
    assert datastore.data['watching'][speaker_watch]['tags'] == [speakers]
    assert {camera, film, digital, audio, speakers} <= set(datastore.data['settings']['application']['tags'])


def test_send_test_notification_uses_selected_child_then_parent_then_global(
        client, live_server, measure_memory_usage, datastore_path, monkeypatch):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    groups = datastore.data['settings']['application']['tags']
    group_urls = {
        camera: ['json://example.invalid/parent'],
        film: ['json://example.invalid/film'],
        digital: ['json://example.invalid/digital'],
    }
    for group_uuid, urls in group_urls.items():
        groups[group_uuid]['notification_urls'] = urls
    datastore.data['settings']['application']['notification_urls'] = ['json://example.invalid/global']
    datastore.add_watch('https://example.com/digital', tag='digital-camera', extras={'paused': True})
    film_watch = datastore.add_watch('https://example.com/film', tag='film', extras={'paused': True})
    datastore.data['watching'][film_watch]['notification_urls'] = ['json://example.invalid/stored-watch']

    sent = []

    def capture_notification(payload, _datastore):
        sent.append(dict(payload))

    monkeypatch.setattr('changedetectionio.notification.handler.process_notification', capture_notification)
    monkeypatch.setattr('apprise.Apprise.add', lambda self, url: True)

    watch_test_url = url_for('ui.ui_notification.ajax_callback_send_notification_test', watch_uuid=film_watch)
    group_test_url = url_for('ui.ui_notification.ajax_callback_send_notification_test', mode='group-settings')

    for expected_urls in (
            ['json://example.invalid/film'],
            ['json://example.invalid/parent'],
            ['json://example.invalid/global']):
        watch_response = client.post(watch_test_url, data={'notification_urls': '', 'tags': 'film'})
        group_response = client.post(group_test_url, data={'notification_urls': '', 'group_uuid': film})
        assert watch_response.status_code == group_response.status_code == 200
        assert len(sent) >= 2
        assert sent[-2]['notification_urls'] == expected_urls
        assert sent[-1]['notification_urls'] == expected_urls
        assert sent[-1]['uuid'] == film_watch  # Group test must use a watch from its own branch.

        if expected_urls == group_urls[film]:
            groups[film]['notification_urls'] = []
        elif expected_urls == group_urls[camera]:
            groups[camera]['notification_urls'] = []


def test_quick_add_from_child_page_keeps_group_and_return_location(
        client, live_server, measure_memory_usage, datastore_path):
    from lxml import html

    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    page = client.get(url_for('watchlist.index', tag=film))
    assert page.status_code == 200
    form = html.fromstring(page.data).get_element_by_id('new-watch-form')
    action = form.get('action')
    group_input = form.xpath('.//input[@name="tags"]')
    assert len(group_input) == 1
    assert group_input[0].get('value') == film
    assert parse_qs(urlparse(action).query)['tag'] == [film]

    response = client.post(action, data={
        'url': 'https://example.com/quick-film',
        'tags': group_input[0].get('value'),
        # Keep it paused while checking the redirect; no external fetch is needed.
        'edit_and_watch_submit_button': '1',
    })
    assert response.status_code == 302
    assert parse_qs(urlparse(response.location).query)['tag'] == [film]
    watch_uuid = next(iter(datastore.data['watching']))
    assert datastore.data['watching'][watch_uuid]['tags'] == [film]


def test_unknown_group_uuid_filters_to_empty_and_does_not_recheck_every_watch(
        client, live_server, measure_memory_usage, datastore_path):
    from changedetectionio import worker_pool

    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    film_watch = datastore.add_watch('https://example.com/film', tag='film', extras={
        'title': 'unique-film-watch-title', 'paused': True,
    })
    other_watch = datastore.add_watch('https://example.com/other', extras={
        'title': 'unique-other-watch-title', 'paused': True,
    })
    unknown_group = str(uuid4())

    assert filters.matching_watch_uuids(datastore, {'tag': film}) == [film_watch]
    assert filters.matching_watch_uuids(datastore, {'tag': unknown_group}) == []
    uuids_response = client.get(url_for('watchlist.uuids', tag=unknown_group))
    assert uuids_response.status_code == 200
    assert uuids_response.json == {'uuids': []}
    page = client.get(url_for('watchlist.index', tag=unknown_group))
    assert page.status_code == 404
    assert b'unique-film-watch-title' not in page.data
    assert b'unique-other-watch-title' not in page.data

    # Recheck must see no watches under the stale/foreign group UUID.
    for watch_uuid in (film_watch, other_watch):
        watch = datastore.data['watching'][watch_uuid]
        watch['paused'] = False
        watch['last_checked'] = int(time.time())
    queued = []
    with patch.object(worker_pool, 'queue_item_async_safe', side_effect=lambda q, item: queued.append(item.item['uuid'])):
        response = client.post(url_for('ui.form_watch_checknow', tag=unknown_group))
    assert response.status_code == 302
    assert queued == []


def test_child_and_parent_ui_recheck_queue_only_their_visible_watches(
        client, live_server, measure_memory_usage, datastore_path):
    from lxml import html
    from changedetectionio import worker_pool
    from changedetectionio.flask_app import update_q

    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    direct_watch = datastore.add_watch('https://example.com/camera', tag='camera', extras={'paused': True})
    film_watch = datastore.add_watch('https://example.com/film', tag='film', extras={'paused': True})
    digital_watch = datastore.add_watch('https://example.com/digital', tag='digital-camera', extras={'paused': True})
    other_watch = datastore.add_watch('https://example.com/other', extras={'paused': True})

    for tag_uuid, expected in ((film, {film_watch}),
                               (digital, {digital_watch}),
                               (camera, {direct_watch, film_watch, digital_watch})):
        page = client.get(url_for('watchlist.index', tag=tag_uuid))
        assert page.status_code == 200
        rows = html.fromstring(page.data).xpath('//tr[@data-watch-uuid]/@data-watch-uuid')
        assert set(rows) == expected

    for watch_uuid in (direct_watch, film_watch, digital_watch, other_watch):
        watch = datastore.data['watching'][watch_uuid]
        watch['paused'] = False
        watch['last_checked'] = int(time.time())

    queued = []

    def capture_enqueue(queue, item):
        queued.append(item.item['uuid'])
        return True

    with patch.object(update_q, 'get_queued_uuids', side_effect=lambda: set(queued)), \
            patch.object(worker_pool, 'get_running_uuids', return_value=set()), \
            patch.object(worker_pool, 'queue_item_async_safe', side_effect=capture_enqueue):
        for tag_uuid, expected in ((film, {film_watch}),
                                   (digital, {film_watch, digital_watch}),
                                   (camera, {direct_watch, film_watch, digital_watch})):
            response = client.post(url_for('ui.form_watch_checknow', tag=tag_uuid))
            assert response.status_code == 302
            assert set(queued) == expected

    assert other_watch not in queued
    assert len(queued) == len(set(queued)) == 3


def test_unknown_group_uuid_rejects_watch_edit_before_other_fields_are_written(
        client, live_server, measure_memory_usage, datastore_path):
    from changedetectionio import processors

    client.environ_base['HTTP_ACCEPT_LANGUAGE'] = 'en'
    datastore = live_server.app.config['DATASTORE']
    film = datastore.add_tag('film')
    watch_uuid = datastore.add_watch('https://example.com/restock', tag='film', extras={
        'processor': 'restock_diff', 'paused': True, 'ignore_text': ['keep-this-text'],
    })
    watch = datastore.data['watching'][watch_uuid]
    assert processors.save_processor_config(datastore, watch_uuid, {
        'restock_diff': {'in_stock_processing': 'all_changes', 'price_change_min': 123},
    })
    config_path = Path(datastore_path) / watch_uuid / 'restock_diff.json'
    before_config = config_path.read_bytes()
    before_ignore = list(watch['ignore_text'])

    response = client.post(url_for('ui.ui_edit.edit_page', uuid=watch_uuid), data={
        'url': watch['url'],
        'title': 'should-not-save',
        'processor': 'restock_diff',
        'fetch_backend': 'html_requests',
        'time_between_check_use_default': 'y',
        'tags': str(uuid4()),
        'ignore_text': 'replace-this-text',
        'processor_config_restock_diff-in_stock_processing': 'off',
        'processor_config_restock_diff-price_change_min': '999',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'Group not found' in response.data
    assert datastore.data['watching'][watch_uuid]['tags'] == [film]
    assert datastore.data['watching'][watch_uuid]['ignore_text'] == before_ignore
    assert datastore.data['watching'][watch_uuid]['title'] != 'should-not-save'
    assert config_path.read_bytes() == before_config


def test_deleting_first_legacy_group_does_not_promote_second_group(
        client, live_server, measure_memory_usage, datastore_path):
    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    legacy_watch = datastore.add_watch('https://example.com/legacy-film', tag='film', extras={'paused': True})
    digital_watch = datastore.add_watch('https://example.com/digital', tag='digital-camera', extras={'paused': True})
    datastore.data['watching'][legacy_watch]['tags'] = [film, digital]

    threads = []

    def record_thread(*args, **kwargs):
        thread = Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    with patch('changedetectionio.blueprint.tags.threading.Thread', new=record_thread):
        response = client.post(url_for('tags.delete', uuid=film))
    assert response.status_code == 302
    assert len(threads) == 1
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()

    assert datastore.data['watching'][legacy_watch]['tags'] == []
    assert datastore.get_all_tags_for_watch(legacy_watch) == {}
    assert filters.matching_watch_uuids(datastore, {'tag': digital}) == [digital_watch]
    assert datastore.data['watching'][digital_watch]['tags'] == [digital]


def test_parent_then_child_api_recheck_queues_child_watch_once(
        client, live_server, measure_memory_usage, datastore_path):
    from changedetectionio import worker_pool
    from changedetectionio.flask_app import update_q

    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    direct_watch = datastore.add_watch('https://example.com/camera', tag='camera', extras={'paused': True})
    child_watch = datastore.add_watch('https://example.com/film', tag='film', extras={'paused': True})
    for watch_uuid in (direct_watch, child_watch):
        watch = datastore.data['watching'][watch_uuid]
        watch['paused'] = False
        watch['last_checked'] = int(time.time())

    queued = []
    headers = _api_headers(datastore)

    def capture_enqueue(queue, item):
        queued.append(item.item['uuid'])
        return True

    with patch.object(update_q, 'get_queued_uuids', side_effect=lambda: set(queued)), \
            patch.object(worker_pool, 'get_running_uuids', return_value=set()), \
            patch.object(worker_pool, 'queue_item_async_safe', side_effect=capture_enqueue):
        parent_response = client.get(url_for('tag', uuid=camera, recheck='true'), headers=headers)
        child_response = client.get(url_for('tag', uuid=film, recheck='true'), headers=headers)

    assert parent_response.status_code == child_response.status_code == 200
    assert set(queued) == {direct_watch, child_watch}
    assert queued.count(child_watch) == 1
