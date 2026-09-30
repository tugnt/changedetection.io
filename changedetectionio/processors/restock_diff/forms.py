from wtforms import (
    BooleanField,
    validators,
    FloatField
)
from wtforms import StringField, TextAreaField
from wtforms.fields.choices import RadioField
from wtforms.fields.form import FormField
from wtforms.form import Form
from flask_babel import lazy_gettext as _l

from changedetectionio.forms import processor_text_json_diff_form


class RestockSettingsForm(Form):
    availability_selector = StringField(_l('Availability CSS selector'), [validators.Optional(), validators.Length(max=300)],
                                        render_kw={'placeholder': '.stock-status'})
    in_stock_labels = TextAreaField(_l('In-stock labels (one per line)'), [validators.Optional(), validators.Length(max=1000)],
                                    render_kw={'placeholder': 'In stock'})
    out_of_stock_labels = TextAreaField(_l('Out-of-stock labels (one per line)'), [validators.Optional(), validators.Length(max=1000)],
                                        render_kw={'placeholder': 'Out of stock'})
    price_selector = StringField(_l('Price CSS selector'), [validators.Optional(), validators.Length(max=300)],
                                 render_kw={'placeholder': '.product-price'})
    in_stock_processing = RadioField(label=_l('Re-stock detection'), choices=[
        ('in_stock_only', _l("In Stock only (Out Of Stock -> In Stock only)")),
        ('all_changes', _l("Any availability changes")),
        ('off', _l("Off, don't follow availability/restock")),
    ], default="in_stock_only")

    price_change_min = FloatField(_l('Below price to trigger notification'), [validators.Optional()],
                                  render_kw={"placeholder": _l("No limit"), "size": "10"})
    price_change_max = FloatField(_l('Above price to trigger notification'), [validators.Optional()],
                                  render_kw={"placeholder": _l("No limit"), "size": "10"})
    price_change_threshold_percent = FloatField(_l('Threshold (%) for price changes since the previous check'), validators=[

        validators.Optional(),
        validators.NumberRange(min=0, max=100, message=_l("Should be between 0 and 100")),
    ], render_kw={"placeholder": "0%", "size": "5"})

    follow_price_changes = BooleanField(_l('Follow price changes'), default=True)

class processor_settings_form(processor_text_json_diff_form):
    processor_config_restock_diff = FormField(RestockSettingsForm)

    def extra_tab_content(self):
        return _l('Restock & Price Detection')

    def extra_form_content(self):
        output = ""

        if getattr(self, 'watch', None) and getattr(self, 'datastore'):
            from changedetectionio.grouping import direct_group_for_watch
            direct = direct_group_for_watch(self.watch, self.datastore.data['settings']['application']['tags'])
            if direct and direct[1].get('overrides_watch'):
                # @todo - Quick and dirty, cant access 'url_for' here because its out of scope somehow
                output = f"""<p><strong>Note! A Group tag overrides the restock and price detection here.</strong></p><style>#restock-fieldset-price-group {{ opacity: 0.6; }}</style>"""

        output += """
        {% from '_helpers.html' import render_field, render_checkbox_field, render_button %}
        <script>
            $(document).ready(function () {
                toggleOpacity('#processor_config_restock_diff-follow_price_changes', '.price-change-minmax', true);
            });
        </script>

        <fieldset id="restock-fieldset-price-group">
            <div class="pure-control-group">
                <fieldset class="pure-group">
                    <legend>Product extraction rules</legend>
                    {{ render_field(form.processor_config_restock_diff.availability_selector) }}
                    {{ render_field(form.processor_config_restock_diff.in_stock_labels) }}
                    {{ render_field(form.processor_config_restock_diff.out_of_stock_labels) }}
                    {{ render_field(form.processor_config_restock_diff.price_selector) }}
                    <span class="pure-form-message-inline">Labels match text within the availability selector. Conflicting or missing labels give an Unknown result.</span>
                </fieldset>
                <fieldset class="pure-group inline-radio">
                    {{ render_field(form.processor_config_restock_diff.in_stock_processing) }}
                </fieldset>
                <fieldset class="pure-group">
                    {{ render_checkbox_field(form.processor_config_restock_diff.follow_price_changes) }}
                    <span class="pure-form-message-inline">Changes in price should trigger a notification</span>
                </fieldset>
                <fieldset class="pure-group price-change-minmax">
                    {{ render_field(form.processor_config_restock_diff.price_change_min, placeholder=watch.get('restock', {}).get('price')) }}
                    <span class="pure-form-message-inline">Minimum amount, Trigger a change/notification when the price drops <i>below</i> this value.</span>
                </fieldset>
                <fieldset class="pure-group price-change-minmax">
                    {{ render_field(form.processor_config_restock_diff.price_change_max, placeholder=watch.get('restock', {}).get('price')) }}
                    <span class="pure-form-message-inline">Maximum amount, Trigger a change/notification when the price rises <i>above</i> this value.</span>
                </fieldset>
                <fieldset class="pure-group price-change-minmax">
                    {{ render_field(form.processor_config_restock_diff.price_change_threshold_percent) }}
                    <span class="pure-form-message-inline">Price must change more than this % since the previous check to trigger a change.</span><br>
                    <span class="pure-form-message-inline">For example, if the previous check saw the product at $1,000 USD, <strong>2%</strong> would mean it has to change more than $20 since then.</span><br>
                </fieldset>
            </div>
        </fieldset>
        {% if restock_test_url is defined and restock_test_url %}
        <fieldset class="pure-group">
            <legend>Test watch</legend>
            <button type="button" class="pure-button" id="test-restock-watch">Test current rules</button>
            <pre id="test-restock-result" aria-live="polite" style="white-space:pre-wrap"></pre>
        </fieldset>
        <script>
        document.getElementById('test-restock-watch').addEventListener('click', async function () {
            const button = this, output = document.getElementById('test-restock-result');
            button.disabled = true;
            output.textContent = 'Fetching...';
            try {
                const form = button.closest('form');
                const response = await fetch({{ restock_test_url|tojson }}, {
                    method: 'POST', credentials: 'same-origin', body: new FormData(form)
                });
                if (!response.ok) throw new Error('Test request failed: HTTP ' + response.status);
                const data = await response.json();
                output.textContent = [
                    'State: ' + data.state, 'HTTP: ' + (data.http_status ?? '—'),
                    'Fetcher: ' + (data.fetcher ?? '—'), 'Proxy: ' + data.proxy,
                    'Source: ' + (data.source ?? '—'), 'Availability: ' + (data.availability ?? '—'),
                    'Price: ' + (data.price ?? '—'), 'Evidence: ' + JSON.stringify(data.evidence, null, 2),
                    data.error ? 'Error: ' + data.error : ''
                ].join('\\n');
            } catch (error) { output.textContent = String(error); }
            finally { button.disabled = false; }
        });
        </script>
        {% endif %}
        """
        return output
