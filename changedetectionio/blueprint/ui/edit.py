from copy import deepcopy
import os
import importlib.resources
from flask import Blueprint, request, redirect, url_for, flash, render_template, abort
from flask_babel import gettext
from loguru import logger
from jinja2 import Environment, FileSystemLoader

from changedetectionio.store import ChangeDetectionStore
from changedetectionio.auth_decorator import login_optionally_required
from changedetectionio.time_handler import default_timezone_name, is_within_schedule
from changedetectionio import worker_pool
from changedetectionio.llm.evaluator import get_llm_config as _get_llm_config

def construct_blueprint(datastore: ChangeDetectionStore, update_q, queuedWatchMetaData):
    edit_blueprint = Blueprint('ui_edit', __name__, template_folder="../ui/templates")

    def _watch_tags(watch):
        """The one direct group whose settings can affect this watch."""
        from changedetectionio.grouping import direct_group_for_watch
        tags = datastore.data['settings']['application'].get('tags', {})
        direct = direct_group_for_watch(watch, tags)
        return [direct] if direct else []

    def _resolve_llm_group_overrides(watch, datastore) -> dict:
        """
        For each LLM field (llm_intent, llm_change_summary): if the watch has no own
        value but a linked group does, return {'value': ..., 'group_name': ..., 'group_uuid': ...}
        so the edit template can show the inherited value as the textarea placeholder and link
        back to the group that supplied it.
        Returns None for each field when the watch has its own value (nothing inherited).

        Only groups whose AI setting is "On" lend their prompts — the same gate the evaluator
        applies via resolve_llm_field(), so the placeholder always reflects what will actually
        run. See llm/evaluator.py:tag_llm_decision().
        """
        from changedetectionio.llm.evaluator import tag_llm_applies_to_watches, tag_llm_decision

        result = {'llm_intent': None, 'llm_change_summary': None, 'llm_backend_profile': None}

        # AI on/off is not a "fill in the blank" field: a group that has taken the decision
        # (On or Off, i.e. not "leave it to each watch") decides for every watch in it (#4204),
        # so report it and let the template show the watch's own checkbox as overridden.
        for tag_uuid, tag in _watch_tags(watch):
            if tag_llm_decision(tag) is not None:
                result['llm_backend_profile'] = {
                    'value': tag_llm_decision(tag),
                    'group_name': tag.get('title', 'tag'),
                    'group_uuid': tag_uuid,
                }
                break

        for field in ('llm_intent', 'llm_change_summary'):
            if (watch.get(field) or '').strip():
                continue  # watch has its own value — editable, no group override
            for tag_uuid, tag in _watch_tags(watch):
                if not tag_llm_applies_to_watches(tag):
                    continue
                if (tag.get(field) or '').strip():
                    result[field] = {
                        'value': tag.get(field).strip(),
                        'group_name': tag.get('title', 'tag'),
                        'group_uuid': tag_uuid,
                    }
                    break
        return result

    def _watch_has_tag_options_set(watch):
        """This should be fixed better so that Tag is some proper Model, a tag is just a Watch also"""
        for tag_uuid, tag in _watch_tags(watch):
            if tag.get('include_filters') or tag.get('subtractive_selectors'):
                return True

    @edit_blueprint.route("/edit/<uuid_str:uuid>", methods=['GET', 'POST'])
    @login_optionally_required
    # https://stackoverflow.com/questions/42984453/wtforms-populate-form-with-data-if-data-exists
    # https://wtforms.readthedocs.io/en/3.0.x/forms/#wtforms.form.Form.populate_obj ?
    def edit_page(uuid):
        from changedetectionio import forms
        from changedetectionio.browser_steps.browser_steps import browser_step_ui_config
        from changedetectionio import processors
        import importlib

        if uuid == 'first':
            uuid = list(datastore.data['watching'].keys()).pop()
        # More for testing, possible to return the first/only
        if not datastore.data['watching'].keys():
            flash(gettext("No watches to edit"), "error")
            return redirect(url_for('watchlist.index'))

        if not uuid in datastore.data['watching']:
            flash(gettext("No watch with the UUID {} found.").format(uuid), "error")
            return redirect(url_for('watchlist.index'))

        switch_processor = request.args.get('switch_processor')
        if switch_processor:
            for p in processors.available_processors():
                if p[0] == switch_processor:
                    datastore.data['watching'][uuid]['processor'] = switch_processor
                    flash(gettext("Switched to mode - {}.").format(p[1]))
                    datastore.clear_watch_history(uuid)
                    redirect(url_for('ui_edit.edit_page', uuid=uuid))

        # be sure we update with a copy instead of accidently editing the live object by reference
        default = None
        while not default:
            try:
                default = deepcopy(datastore.data['watching'][uuid])
            except RuntimeError as e:
                # Dictionary changed
                continue

        # Defaults for proxy choice
        if datastore.proxy_list is not None:  # When enabled
            # @todo
            # Radio needs '' not None, or incase that the chosen one no longer exists
            if default['proxy'] is None or not any(default['proxy'] in tup for tup in datastore.proxy_list):
                default['proxy'] = ''
        # proxy_override set to the json/text list of the items

        # Does it use some custom form? does one exist?
        processor_name = datastore.data['watching'][uuid].get('processor', '')
        processor_classes = next((tpl for tpl in processors.find_processors() if tpl[1] == processor_name), None)
        if not processor_classes:
            flash(gettext("Could not load '{}' processor, processor plugin might be missing. Please select a different processor.").format(processor_name), 'error')
            # Fall back to default processor so user can still edit and change processor
            processor_classes = next((tpl for tpl in processors.find_processors() if tpl[1] == 'text_json_diff'), None)
            if not processor_classes:
                # If even text_json_diff is missing, something is very wrong
                flash(gettext("Could not load '{}' processor, processor plugin might be missing.").format(processor_name), 'error')
                return redirect(url_for('watchlist.index'))

        parent_module = processors.get_parent_module(processor_classes[0])

        try:
            # Get the parent of the "processor.py" go up one, get the form (kinda spaghetti but its reusing existing code)
            forms_module = importlib.import_module(f"{parent_module.__name__}.forms")
            # Access the 'processor_settings_form' class from the 'forms' module
            form_class = getattr(forms_module, 'processor_settings_form')
        except ModuleNotFoundError as e:
            # .forms didnt exist
            form_class = forms.processor_text_json_diff_form
        except AttributeError as e:
            # .forms exists but no useful form
            form_class = forms.processor_text_json_diff_form

        form = form_class(formdata=request.form if request.method == 'POST' else None,
                          data=default,
                          extra_notification_tokens=default.extra_notification_token_values(),
                          default_system_settings=datastore.data['settings']
                          )

        # For the form widget tag UUID back to "string name" for the field
        form.tags.datastore = datastore

        # Used by some forms that need to dig deeper
        form.datastore = datastore
        form.watch = default

        # Load processor-specific config from JSON file for GET requests
        if request.method == 'GET' and processor_name:
            try:
                from changedetectionio.processors.base import difference_detection_processor
                # Create a processor instance to access config methods
                processor_instance = difference_detection_processor(datastore, uuid)
                # Use processor name as filename so each processor keeps its own config
                config_filename = f'{processor_name}.json'
                processor_config = processor_instance.get_extra_watch_config(config_filename)

                if processor_config:
                    from wtforms.fields.form import FormField
                    # Populate processor-config-* fields from JSON
                    for config_key, config_value in processor_config.items():
                        if not isinstance(config_value, dict):
                            continue
                        # Try exact API-named field first (e.g., processor_config_restock_diff)
                        target_field = getattr(form, f'processor_config_{config_key}', None)
                        # Fallback: find any FormField sub-form whose fields cover config_value keys
                        if target_field is None:
                            for form_field in form:
                                if isinstance(form_field, FormField) and all(k in form_field.form._fields for k in config_value):
                                    target_field = form_field
                                    break
                        if target_field is not None:
                            for sub_key, sub_value in config_value.items():
                                sub_field = target_field.form._fields.get(sub_key)
                                if sub_field is not None:
                                    sub_field.data = sub_value
                                    logger.debug(f"Loaded processor config from {config_filename}: {sub_key} = {sub_value}")
            except Exception as e:
                logger.warning(f"Failed to load processor config: {e}")

        for p in datastore.extra_browsers:
            form.fetch_backend.choices.append(p)

        form.fetch_backend.choices.append(("system", gettext('System settings default')))

        # form.browser_steps[0] can be assumed that we 'goto url' first

        if datastore.proxy_list is None:
            # @todo - Couldn't get setattr() etc dynamic addition working, so remove it instead
            del form.proxy
        else:
            form.proxy.choices = [('', gettext('Default'))]
            for p in datastore.proxy_list:
                form.proxy.choices.append(tuple((p, datastore.proxy_list[p]['label'])))


        if request.method == 'POST' and form.validate():

            # Resolve the submitted group before any watch or processor config is
            # changed. An unknown UUID must reject the whole edit.
            submitted_group = form.data.get('tags') or ''
            if isinstance(submitted_group, list):
                submitted_group = submitted_group[0] if submitted_group else ''
            first_group = submitted_group.split(',')[0].strip()
            group_uuid = None
            if first_group:
                groups = datastore.data['settings']['application'].get('tags', {})
                group_uuid = first_group if first_group in groups else datastore.tag_uuid_for_title(first_group)
                if not group_uuid:
                    from uuid import UUID
                    try:
                        UUID(first_group)
                    except ValueError:
                        group_uuid = datastore.add_tag(title=first_group)
                    else:
                        flash(gettext('Group not found'), 'error')
                        return redirect(url_for('ui.ui_edit.edit_page', uuid=uuid))

            extra_update_obj = {
                'consecutive_filter_failures': 0,
                'consecutive_access_blocks': 0,
                'last_error' : False
            }

            if request.args.get('unpause_on_save'):
                extra_update_obj['paused'] = False

            extra_update_obj['time_between_check'] = form.time_between_check.data

            # Handle processor-config-* fields separately (save to JSON, not datastore)
            # IMPORTANT: These must NOT be saved to url-watches.json, only to the processor-specific JSON file
            processor_config_data = processors.extract_processor_config_from_form_data(form.data)
            processors.save_processor_config(datastore, uuid, processor_config_data)

            # Ignore text
            form_ignore_text = form.ignore_text.data
            datastore.data['watching'][uuid]['ignore_text'] = form_ignore_text

            # Be sure proxy value is None
            if datastore.proxy_list is not None and form.data['proxy'] == '':
                extra_update_obj['proxy'] = None

            # Unsetting all filter_text methods should make it go back to default
            # This particularly affects tests running
            if 'filter_text_added' in form.data and not form.data.get('filter_text_added') \
                    and 'filter_text_replaced' in form.data and not form.data.get('filter_text_replaced') \
                    and 'filter_text_removed' in form.data and not form.data.get('filter_text_removed'):
                extra_update_obj['filter_text_added'] = True
                extra_update_obj['filter_text_replaced'] = True
                extra_update_obj['filter_text_removed'] = True

            # A group that has taken the AI on/off decision owns that control, so the edit page
            # renders it disabled (see include_llm_intent.html). A disabled checkbox isn't
            # submitted at all, and for a checkbox "not submitted" is indistinguishable from
            # "unticked" — so don't take this field from the form while a group decides. The
            # watch keeps its own preference untouched, ready for when the group stops deciding.
            # Resolved against the watch's *stored* tags — i.e. what the page was rendered from,
            # so attaching or detaching a group in this same save is still honoured correctly.
            if _resolve_llm_group_overrides(datastore.data['watching'][uuid], datastore).get('llm_backend_profile'):
                extra_update_obj['llm_backend_profile'] = datastore.data['watching'][uuid].get('llm_backend_profile', True)

            extra_update_obj['tags'] = [group_uuid] if group_uuid else []

            datastore.data['watching'][uuid].update(form.data)
            datastore.data['watching'][uuid].update(extra_update_obj)

            if not datastore.data['watching'][uuid].get('tags'):
                # Force it to be a list, because form.data['tags'] will be string if nothing found
                # And del(form.data['tags'] ) wont work either for some reason
                datastore.data['watching'][uuid]['tags'] = []

            # Recast it if need be to right data Watch handler
            watch_class = processors.get_custom_watch_obj_for_processor(form.data.get('processor'))
            datastore.data['watching'][uuid] = watch_class(datastore_path=datastore.datastore_path, __datastore=datastore.data, default=datastore.data['watching'][uuid])

            # Save the watch immediately
            datastore.data['watching'][uuid].commit()

            flash(gettext("Updated watch - unpaused!") if request.args.get('unpause_on_save') else gettext("Updated watch."))

            # Cleanup any browsersteps session for this watch
            try:
                from changedetectionio.blueprint.browser_steps import cleanup_session_for_watch
                cleanup_session_for_watch(uuid)
            except Exception as e:
                logger.debug(f"Error cleaning up browsersteps session: {e}")

            # Do not queue on edit if its not within the time range

            # @todo maybe it should never queue anyway on edit...
            is_in_schedule = True
            watch = datastore.data['watching'].get(uuid)

            if watch.get('time_between_check_use_default'):
                time_schedule_limit = datastore.data['settings']['requests'].get('time_schedule_limit') or {}
            else:
                time_schedule_limit = watch.get('time_schedule_limit') or {}

            tz_name = default_timezone_name(
                time_schedule_limit.get('timezone')
                or datastore.data['settings']['application'].get('scheduler_timezone_default')
            )

            if time_schedule_limit and time_schedule_limit.get('enabled'):
                try:
                    is_in_schedule = is_within_schedule(time_schedule_limit=time_schedule_limit,
                                                      default_tz=tz_name
                                                      )
                except Exception as e:
                    # Only decides whether to queue an immediate recheck — the watch is
                    # already saved by this point. Returning a bare `False` from a view
                    # made Flask raise TypeError and the save appeared to fail with a 500.
                    logger.error(
                        f"{uuid} - Recheck scheduler, error handling timezone, check skipped - TZ name '{tz_name}' - {str(e)}")
                    is_in_schedule = False

            #############################
            if not datastore.data['watching'][uuid].get('paused') and is_in_schedule:
                # Queue the watch for immediate recheck, with a higher priority
                worker_pool.queue_item_async_safe(update_q, queuedWatchMetaData.PrioritizedItem(priority=1, item={'uuid': uuid}))

            # Diff page [edit] link should go back to diff page
            if request.args.get("next") and request.args.get("next") == 'diff':
                return redirect(url_for('ui.ui_diff.diff_history_page', uuid=uuid))

            active_tag = request.args.get('tag', '')
            if active_tag:
                groups = datastore.data['settings']['application'].get('tags', {})
                active_group_uuid = active_tag if active_tag in groups else datastore.tag_uuid_for_title(active_tag)
                # When moving a watch, return to the destination group if the old
                # child or parent view can no longer show it.
                if active_group_uuid and active_group_uuid not in datastore.get_group_path_for_watch(uuid):
                    active_tag = group_uuid or ''

            return redirect(url_for('watchlist.index', tag=active_tag))

        else:
            if request.method == 'POST' and not form.validate():
                flash(gettext("An error occurred, please see below."), "error")

            # JQ is difficult to install on windows and must be manually added (outside requirements.txt)
            jq_support = True
            try:
                import jq
            except ModuleNotFoundError:
                jq_support = False

            watch = datastore.data['watching'].get(uuid)

            from zoneinfo import available_timezones

            # Import the global plugin system
            from changedetectionio.pluggy_interface import collect_ui_edit_stats_extras, get_fetcher_capabilities

            # Get fetcher capabilities instead of hardcoded logic
            capabilities = get_fetcher_capabilities(watch, datastore)

            # Add processor capabilities from module
            capabilities['supports_visual_selector'] = getattr(parent_module, 'supports_visual_selector', False)
            capabilities['supports_text_filters_and_triggers'] = getattr(parent_module, 'supports_text_filters_and_triggers', False)
            capabilities['supports_text_filters_and_triggers_elements'] = getattr(parent_module, 'supports_text_filters_and_triggers_elements', False)
            capabilities['supports_request_type'] = getattr(parent_module, 'supports_request_type', False)

            app_rss_token = datastore.data['settings']['application'].get('rss_access_token'),

            c = [f"processor-{watch.get('processor')}"]
            if worker_pool.is_watch_running(uuid):
                c.append('checking-now')

            template_args = {
                'available_processors': processors.available_processors(),
                'available_timezones': sorted(available_timezones()),
                'browser_steps_config': browser_step_ui_config,
                'emailprefix': os.getenv('NOTIFICATION_MAIL_BUTTON_PREFIX', False),
                'extra_classes': ' '.join(c),
                'extra_notification_token_placeholder_info': datastore.get_unique_notification_token_placeholders_available(),
                'extra_processor_config': form.extra_tab_content(),
                'extra_title': f" - {gettext('Edit')} - {watch.label}",
                'form': form,
                'has_default_notification_urls': True if len(datastore.data['settings']['application']['notification_urls']) else False,
                'has_extra_headers_file': len(datastore.get_all_headers_in_textfile_for_watch(uuid=uuid)) > 0,
                'has_special_tag_options': _watch_has_tag_options_set(watch=watch),
                'jq_support': jq_support,
                'playwright_enabled': os.getenv('PLAYWRIGHT_DRIVER_URL', False),
                'app_rss_token': app_rss_token,
                'rss_uuid_feed' : {
                    'label': watch.label,
                    'url': url_for('rss.rss_single_watch', uuid=watch['uuid'], token=app_rss_token)
                },
                'settings_application': datastore.data['settings']['application'],
                'ui_edit_stats_extras': collect_ui_edit_stats_extras(watch),
                'visual_selector_data_ready': datastore.visualselector_data_is_ready(watch_uuid=uuid),
                'timezone_default_config': datastore.data['settings']['application'].get('scheduler_timezone_default'),
                'using_global_webdriver_wait': not default['webdriver_delay'],
                'uuid': uuid,
                'restock_test_url': url_for('ui.ui_edit.test_restock', uuid=uuid),
                'watch': watch,
                'capabilities': capabilities,
                'auto_applied_tags': {
                    tag_uuid: tag
                    for tag_uuid, tag in datastore.get_all_tags_for_watch(uuid).items()
                    if tag_uuid not in watch.get('tags', [])
                },
                # LLM intent context
                'llm_configured': bool(_get_llm_config(datastore)),
                'llm_group_overrides': _resolve_llm_group_overrides(watch, datastore),
            }

            included_content = None
            if form.extra_form_content():
                # So that the extra panels can access _helpers.html etc, we set the environment to load from templates/
                # And then render the code from the module
                templates_dir = str(importlib.resources.files("changedetectionio").joinpath('templates'))
                env = Environment(loader=FileSystemLoader(templates_dir))
                template = env.from_string(form.extra_form_content())
                included_content = template.render(**template_args)

            output = render_template("edit.html",
                                     extra_tab_content=form.extra_tab_content() if form.extra_tab_content() else None,
                                     extra_form_content=included_content,
                                     **template_args
                                     )

        return output

    @edit_blueprint.route("/edit/<uuid_str:uuid>/get-html", methods=['GET'])
    @login_optionally_required
    def watch_get_latest_html(uuid):
        from io import BytesIO
        from flask import send_file
        import brotli

        if uuid == 'first':
            uuid = list(datastore.data['watching'].keys()).pop()
        watch = datastore.data['watching'].get(uuid)
        if watch and watch.history.keys() and os.path.isdir(watch.data_dir):
            latest_filename = list(watch.history.keys())[-1]
            html_fname = os.path.join(watch.data_dir, f"{latest_filename}.html.br")
            with open(html_fname, 'rb') as f:
                if html_fname.endswith('.br'):
                    # Read and decompress the Brotli file
                    decompressed_data = brotli.decompress(f.read())
                else:
                    decompressed_data = f.read()

            buffer = BytesIO(decompressed_data)

            return send_file(buffer, as_attachment=True, download_name=f"{latest_filename}.html", mimetype='text/html')

        # Return a 500 error
        abort(500)

    @edit_blueprint.route('/edit/<uuid_str:uuid>/test-restock', methods=['POST'])
    @login_optionally_required
    def test_restock(uuid):
        """Run a read-only fetch and extraction for the edit page."""
        import asyncio
        from flask import jsonify
        from changedetectionio.processors.restock_diff.processor import perform_site_check
        from changedetectionio.content_fetchers.exceptions import (
            Non200ErrorCodeReceived, BlockPageReceived)

        watch = datastore.data['watching'].get(uuid)
        if not watch or watch.get('processor') != 'restock_diff':
            abort(404)
        checker = perform_site_check(datastore=datastore, watch_uuid=uuid)
        from changedetectionio.content_fetchers import resolve_content_fetcher
        _, backend_name, _ = resolve_content_fetcher(watch=checker.watch, datastore=datastore)
        settings = dict(checker.get_restock_settings(checker.watch))
        group_overrides = any(tag.get('overrides_watch') for _, tag in _watch_tags(checker.watch))
        if not group_overrides:
            for field in ('availability_selector', 'price_selector', 'in_stock_labels', 'out_of_stock_labels'):
                key = f'processor_config_restock_diff-{field}'
                if key in request.form:
                    settings[field] = request.form[key][:1000]
        checker.diagnostic_settings = settings
        proxy_id = datastore.get_preferred_proxy_for_watch(uuid=uuid)
        proxy_entry = (datastore.proxy_list or {}).get(proxy_id) or {}
        result = {'state': 'unknown', 'http_status': None, 'fetcher': backend_name,
                  'proxy': str(proxy_entry.get('label') or 'No proxy')[:100],
                  'source': None, 'price': None, 'availability': None,
                  'evidence': [], 'error': None}
        try:
            asyncio.run(checker.call_browser(diagnostic=True))
            result['http_status'] = checker.fetcher.get_last_status_code()
            result['fetcher'] = checker.fetcher.backend_name
            data, multiple, configured, source = checker.extract_product_data(checker.watch)
            if data.get('price') is None or data.get('availability') is None:
                from changedetectionio.pluggy_interface import get_itemprop_availability_from_plugin
                from changedetectionio.llm.evaluator import llm_enabled_for_watch, resolve_intent
                llm_on, _ = llm_enabled_for_watch(checker.watch, datastore)
                intent, _ = resolve_intent(checker.watch, datastore) if llm_on else ('', '')
                plugin_data = get_itemprop_availability_from_plugin(
                    checker.fetcher.content, checker.fetcher.backend_name,
                    checker.fetcher, checker.watch.link, llm_intent=intent or None)
                if plugin_data:
                    data = {key: value for key, value in plugin_data.items() if not key.startswith('_')}
                    source = 'Fetcher plugin'
            if configured['availability'] is not None:
                data['availability'] = configured['availability']
            elif settings.get('availability_selector'):
                data.pop('availability', None)
            if configured['price'] is not None:
                data['price'] = configured['price']
            result['source'] = source
            result['evidence'] = configured['evidence']
            result['price'] = data.get('price')
            result['availability'] = data.get('availability')
            if settings.get('availability_selector') and configured['availability'] is None:
                result['availability'] = None
            if configured['error']:
                result['error'] = configured['error']
            if multiple and data.get('price') is None:
                result['error'] = 'Multiple prices in product metadata'
            from changedetectionio.processors.restock_diff.rules import availability_state
            state = availability_state(data.get('availability'))
            if state is not None:
                result['state'] = 'in_stock' if state else 'out_of_stock'
            if configured['in_stock'] is not None:
                result['state'] = 'in_stock' if configured['in_stock'] else 'out_of_stock'
            elif not settings.get('availability_selector') and checker.fetcher.instock_data not in (None, 'Possibly in stock'):
                result['state'] = 'out_of_stock'
            if not result['evidence'] and data:
                result['evidence'] = [{'source': source, 'field': 'product metadata',
                                       'matches': [str(data.get('availability') or '')[:300],
                                                   str(data.get('price') or '')[:100]]}]
            if not data and not result['error']:
                result['error'] = 'No stock or price data found'
        except BlockPageReceived as exc:
            result.update(state='blocked', http_status=exc.status_code,
                          error=f'Blocked by {exc.provider} challenge page')
        except Non200ErrorCodeReceived as exc:
            result.update(state='blocked' if exc.status_code in (403, 429) else 'unknown',
                          http_status=exc.status_code, error=f'HTTP {exc.status_code}')
        except Exception as exc:
            logger.warning(f'Restock test failed for {uuid}: {exc}')
            # Fetcher exceptions may contain a proxy URL with credentials.
            result['error'] = f'{type(exc).__name__}: Fetch or extraction failed; see server logs'
        return jsonify(result)

    @edit_blueprint.route("/edit/<uuid_str:uuid>/get-data-package", methods=['GET'])
    @login_optionally_required
    def watch_get_data_package(uuid):
        """Download all data for a single watch as a zip file"""
        from io import BytesIO
        from flask import send_file
        import zipfile
        from pathlib import Path
        import datetime

        watch = datastore.data['watching'].get(uuid)
        if not watch:
            abort(404)

        # Create zip in memory
        memory_file = BytesIO()

        with zipfile.ZipFile(memory_file, 'w',
                           compression=zipfile.ZIP_DEFLATED,
                           compresslevel=8) as zipObj:

            # Add the watch's JSON file if it exists
            watch_json_path = os.path.join(watch.data_dir, 'watch.json')
            if os.path.isfile(watch_json_path):
                zipObj.write(watch_json_path,
                           arcname=os.path.join(uuid, 'watch.json'),
                           compress_type=zipfile.ZIP_DEFLATED,
                           compresslevel=8)

            # Add all files in the watch data directory
            if os.path.isdir(watch.data_dir):
                for f in Path(watch.data_dir).glob('*'):
                    if f.is_file() and f.name != 'watch.json':  # Skip watch.json since we already added it
                        zipObj.write(f,
                                   arcname=os.path.join(uuid, f.name),
                                   compress_type=zipfile.ZIP_DEFLATED,
                                   compresslevel=8)

        # Seek to beginning of file
        memory_file.seek(0)

        # Generate filename with timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"watch-data-{uuid[:8]}-{timestamp}.zip"

        return send_file(memory_file,
                        as_attachment=True,
                        download_name=filename,
                        mimetype='application/zip')

    # Ajax callback
    @edit_blueprint.route("/edit/<uuid_str:uuid>/preview-rendered", methods=['POST'])
    @login_optionally_required
    def watch_get_preview_rendered(uuid):
        '''For when viewing the "preview" of the rendered text from inside of Edit'''
        from flask import jsonify

        if uuid == 'first':
            uuid = list(datastore.data['watching'].keys()).pop()
        from changedetectionio.processors.text_json_diff import prepare_filter_prevew
        result = prepare_filter_prevew(watch_uuid=uuid, form_data=request.form, datastore=datastore)
        return jsonify(result)

    @edit_blueprint.route("/highlight_submit_ignore_url", methods=['POST'])
    @login_optionally_required
    def highlight_submit_ignore_url():
        import re
        mode = request.form.get('mode')
        selection = request.form.get('selection')

        uuid = request.args.get('uuid','')
        if datastore.data["watching"].get(uuid):
            # Build the new list and REBIND it. Appending in place bypasses
            # watch_base.__setitem__, so the watch is never flagged as edited and
            # the "content unchanged since last check" skip stays active — the new
            # ignore_text would then not take effect until the page changed on its
            # own. Assigning the key marks the watch edited and forces reprocessing.
            ignore_text = list(datastore.data["watching"][uuid]['ignore_text'])
            if mode == 'exact':
                for l in selection.splitlines():
                    ignore_text.append(l.strip())
            elif mode == 'digit-regex':
                for l in selection.splitlines():
                    # Replace any series of numbers with a regex
                    s = re.escape(l.strip())
                    s = re.sub(r'[0-9]+', r'\\d+', s)
                    ignore_text.append('/' + s + '/')
            datastore.data["watching"][uuid]['ignore_text'] = ignore_text

            # Save the updated ignore_text
            datastore.data["watching"][uuid].commit()

        return f"<a href={url_for('ui.ui_preview.preview_page', uuid=uuid)}>Click to preview</a>"
    
    return edit_blueprint
