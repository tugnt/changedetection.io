"""Existing child groups can be selected from both watch editing screens."""

from flask import url_for
from lxml import html


def test_add_and_edit_watch_show_child_groups_and_edit_can_move_watch(
        client, live_server, measure_memory_usage, datastore_path, monkeypatch):
    from changedetectionio.blueprint.add_watch_ui import browser_config

    datastore = live_server.app.config['DATASTORE']
    camera = datastore.add_tag('camera')
    film = datastore.add_tag('film', parent_uuid=camera)
    digital = datastore.add_tag('digital-camera', parent_uuid=camera)
    monkeypatch.setattr(
        browser_config, 'list_visual_browser_choices',
        lambda datastore: [('html_webdriver', 'WebDriver Chrome/Javascript')],
    )

    add_page = client.get(url_for('add_watch_ui.add_watch_ui_index'))
    assert add_page.status_code == 200
    assert f'data-group-uuid="{film}"'.encode() in add_page.data
    assert f'data-group-uuid="{digital}"'.encode() in add_page.data
    assert b'label="camera / film"' in add_page.data
    assert b'label="camera / digital-camera"' in add_page.data

    url = 'https://example.com/film-product'
    watch_uuid = datastore.add_watch(url, tag='film', extras={'paused': True})
    edit_url = url_for('ui.ui_edit.edit_page', uuid=watch_uuid, tag=film)
    edit_page = client.get(edit_url)
    assert edit_page.status_code == 200
    assert f'data-group-uuid="{digital}"'.encode() in edit_page.data
    assert b'label="camera / film"' in edit_page.data
    group_input = html.fromstring(edit_page.data).xpath('//input[@name="tags"]')[0]
    assert group_input.get('value') == 'film'

    saved = client.post(edit_url, data={
        'url': url,
        'tags': digital,
        'fetch_backend': 'html_requests',
        'time_between_check_use_default': 'y',
    })
    assert saved.status_code == 302
    assert datastore.data['watching'][watch_uuid]['tags'] == [digital]
    assert list(datastore.get_group_path_for_watch(watch_uuid)) == [digital, camera]
    assert f'tag={digital}'.encode() in saved.headers['Location'].encode()
