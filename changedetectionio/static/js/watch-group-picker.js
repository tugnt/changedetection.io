// Keep the group name readable while submitting its UUID. An unfamiliar name
// stays as text so the existing create-new-group flow still works.
(function () {
    let pickerSeq = 0;

    function normalize(value) {
        return (value || '').trim().toLowerCase().replace(/\s*\/\s*/g, '/');
    }

    // "Parent / Child" -> the "Parent" part, so the list can show the hierarchy
    // above the group name instead of one flat string.
    function parentPath(option) {
        const label = option.getAttribute('label') || option.value;
        const suffix = option.value;
        if (label.length > suffix.length && label.endsWith(suffix)) {
            return label.slice(0, label.length - suffix.length).replace(/\s*\/\s*$/, '');
        }
        return '';
    }

    // Replaces the browser's native <datalist> popup, which cannot be styled and
    // renders outside the page. The <datalist> stays in the DOM as the data
    // source and as the no-JS fallback.
    function buildPicker(input, choices) {
        const options = Array.from(choices.options).map(function (option) {
            return { value: option.value, path: parentPath(option), uuid: option.dataset.groupUuid };
        });
        if (!options.length) return;

        const wrap = document.createElement('div');
        wrap.className = 'group-picker';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        const list = document.createElement('ul');
        list.className = 'group-picker__list';
        list.id = 'group-picker-list-' + (++pickerSeq);
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        wrap.appendChild(list);

        input.removeAttribute('list');
        input.setAttribute('role', 'combobox');
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', list.id);

        let matches = [];
        let activeIndex = -1;

        function close() {
            list.hidden = true;
            activeIndex = -1;
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
        }

        function setActive(index) {
            activeIndex = index;
            Array.from(list.children).forEach(function (li, i) {
                const active = i === index;
                li.classList.toggle('group-picker__option--active', active);
                li.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            if (index >= 0) {
                const li = list.children[index];
                input.setAttribute('aria-activedescendant', li.id);
                li.scrollIntoView({ block: 'nearest' });
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        function commit(option) {
            input.value = option.value;
            // Remembers exactly which group was picked, so groups that share a
            // title under different parents still submit the right UUID.
            input.dataset.groupUuid = option.uuid;
            close();
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }

        function open(filterText) {
            const needle = normalize(filterText);
            matches = options.filter(function (option) {
                if (!needle) return true;
                return normalize(option.value).includes(needle) ||
                    normalize(option.path + '/' + option.value).includes(needle);
            });

            if (!matches.length) {
                close();
                return;
            }

            list.textContent = '';
            matches.forEach(function (option, i) {
                const li = document.createElement('li');
                li.className = 'group-picker__option';
                li.id = list.id + '-opt-' + i;
                li.setAttribute('role', 'option');
                li.setAttribute('aria-selected', 'false');

                if (option.path) {
                    const path = document.createElement('span');
                    path.className = 'group-picker__path';
                    path.textContent = option.path;
                    li.appendChild(path);
                }

                const title = document.createElement('span');
                title.className = 'group-picker__title';
                title.textContent = option.value;
                li.appendChild(title);

                // mousedown, not click: it fires before the input's blur, which would
                // otherwise close the list and cancel the selection.
                li.addEventListener('mousedown', function (event) {
                    event.preventDefault();
                    commit(option);
                });
                li.addEventListener('mousemove', function () { setActive(i); });
                list.appendChild(li);
            });

            list.hidden = false;
            list.style.width = input.offsetWidth + 'px';
            input.setAttribute('aria-expanded', 'true');
            setActive(-1);
        }

        input.addEventListener('focus', function () { open(''); });
        input.addEventListener('click', function () { open(input.value); });

        input.addEventListener('input', function () {
            // Typing past a picked group invalidates the remembered UUID.
            delete input.dataset.groupUuid;
            open(input.value);
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (list.hidden) {
                    open(input.value);
                    return;
                }
                const step = event.key === 'ArrowDown' ? 1 : -1;
                const next = activeIndex < 0
                    ? (step === 1 ? 0 : matches.length - 1)
                    : (activeIndex + step + matches.length) % matches.length;
                setActive(next);
            } else if (event.key === 'Enter') {
                if (!list.hidden && activeIndex >= 0) {
                    event.preventDefault();
                    commit(matches[activeIndex]);
                }
            } else if (event.key === 'Escape') {
                if (!list.hidden) {
                    event.stopPropagation();
                    close();
                }
            } else if (event.key === 'Tab') {
                close();
            }
        });

        input.addEventListener('blur', close);
    }

    function initialize() {
        document.querySelectorAll('input[name="tags"][list="available-groups"]').forEach(function (input) {
            const choices = document.getElementById(input.getAttribute('list'));
            if (!choices || !input.form) return;

            input.form.addEventListener('submit', function () {
                const requested = normalize(input.value);
                const picked = Array.from(choices.options).find(function (option) {
                    return input.dataset.groupUuid && option.dataset.groupUuid === input.dataset.groupUuid &&
                        normalize(option.value) === requested;
                });
                const match = picked || Array.from(choices.options).find(function (option) {
                    return requested && (normalize(option.value) === requested ||
                        normalize(option.getAttribute('label')) === requested);
                });
                input.value = match ? match.dataset.groupUuid : input.value.trim();
            }, true);

            buildPicker(input, choices);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }
})();
