/*{# In-page table sort, filter, and paging. A board is a picture: the engine
   draws every row of a table once (`data-dbt-table`) and publishes the hooks
   this reads -- row groups, `data-dbt-sort` keys, header `data-col`, stripes,
   rules, pagers. This script only moves, hides, and relabels those elements;
   it never computes a cell's text, format, or color, never issues a request,
   and never touches the URL. State lives in the page and is not saved. #}*/
(function() {
    var SVG_NS = 'http://www.w3.org/2000/svg';
    var MAX_CHECKLIST_VALUES = 100;
    var _menu = null;

    function _children(parent, attr) {
        return Array.prototype.filter.call(parent.children, function(el) {
            return el.hasAttribute(attr);
        });
    }

    /*{# A wrapped cell is several tspans; joined bare they would run words together. #}*/
    /*{# An anchor affix ($, %, a shared-scale M) is left out: which row shows it must #}*/
    /*{# not change a value's text. #}*/
    function _cellText(el) {
        var lines = Array.prototype.filter.call(el.children, function(child) {
            return child.hasAttribute('y');
        });
        if (lines.length > 1) {
            return lines.map(function(line) { return line.textContent; }).join(' ');
        }
        return Array.prototype.map.call(el.childNodes, function(node) {
            var isAffix = node.nodeType === 1 && node.classList.contains('dbt-table-affix');
            return isAffix ? '' : node.textContent;
        }).join('');
    }

    /*{# The engine always publishes these; a missing one is a contract break, #}*/
    /*{# not a case to paper over with NaN page math. #}*/
    function _numberHook(el, name) {
        var value = el.getAttribute(name);
        if (value === null || value === '' || !isFinite(Number(value))) {
            throw new Error('table runtime: missing or non-numeric ' + name + ' on <' + el.tagName + '>');
        }
        return Number(value);
    }

    function _requireHook(root, selector) {
        var el = root.querySelector(selector);
        if (!el) throw new Error('table runtime: no ' + selector + ' in the pager');
        return el;
    }

    /*{# The words come from the board's language, which the engine publishes on #}*/
    /*{# the table root as `data-dbt-language`; the page cannot fetch the catalog. #}*/
    /*{# Stored renders without the attribute are English (controls_english.js). #}*/
    function _word(table, name) {
        var raw = table.getAttribute('data-dbt-language');
        var words = raw ? JSON.parse(raw) : window.dbtControlsEnglish;
        if (typeof words[name] !== 'string') throw new Error('table runtime: no word ' + name);
        return words[name];
    }

    function _readRow(el) {
        var row = {
            el: el,
            summary: el.hasAttribute('data-dbt-row-role'),
            y: _numberHook(el, 'data-dbt-y'),
            height: _numberHook(el, 'data-dbt-h'),
            cells: {},
            stripe: el.querySelector('[data-dbt-stripe]'),
            rule: el.querySelector('[data-dbt-row-rule]'),
            number: el.querySelector('[data-dbt-row-number]'),
            affixes: Array.prototype.map.call(el.querySelectorAll('.dbt-table-affix'), function(affix) {
                return {
                    el: affix,
                    col: affix.parentNode.getAttribute('data-col'),
                    mask: _numberHook(affix, 'data-dbt-affix'),
                };
            }),
        };
        el.querySelectorAll('[data-col]').forEach(function(cell) {
            if (cell.hasAttribute('data-dbt-row-number')) return;
            row.cells[cell.getAttribute('data-col')] = {
                text: _cellText(cell),
                key: cell.hasAttribute('data-dbt-sort') ? cell.getAttribute('data-dbt-sort') : null,
            };
        });
        return row;
    }

    /*{# A key that is not a number (N/A, an empty string) in a number column is a null. #}*/
    function _sortKey(type, row, col) {
        var cell = row.cells[col];
        if (!cell || cell.key === null) return null;
        if (type === 'text') return cell.key;
        var value = cell.key.trim() === '' ? NaN : Number(cell.key);
        return isFinite(value) ? value : null;
    }

    /*{# Nulls last in both directions; ties keep the query's order. #}*/
    function _sorted(state, rows, col, dir) {
        var type = state.types[col];
        var decorated = rows.map(function(row, i) { return { row: row, key: _sortKey(type, row, col), i: i }; });
        decorated.sort(function(a, b) {
            if (a.key === null || b.key === null) {
                if (a.key === b.key) return a.i - b.i;
                return a.key === null ? 1 : -1;
            }
            var order = type === 'text' ? a.key.localeCompare(b.key) : a.key - b.key;
            return order * dir || a.i - b.i;
        });
        return decorated.map(function(d) { return d.row; });
    }

    function _filterActive(state) {
        return Object.keys(state.filters).length > 0;
    }

    function _passes(state, row) {
        return Object.keys(state.filters).every(function(col) {
            return row.cells[col] && state.filters[col].has(row.cells[col].text);
        });
    }

    function _setHidden(el, hidden) {
        if (hidden) el.setAttribute('display', 'none');
        else el.removeAttribute('display');
    }

    function _layout(state) {
        /*{# Unsorted, every row keeps its place in the query's order. Sorted, the #}*/
        /*{# summary rows (the engine pools only a trailing run) stay last, unsorted. #}*/
        /*{# A summary row was computed over rows a filter now hides. #}*/
        var filtered = _filterActive(state);
        var dataRows = state.rows.filter(function(row) { return !row.summary; });
        var ordered = (state.sort ? _sorted(state, dataRows, state.sort.col, state.sort.dir) : dataRows)
            .filter(function(row) { return _passes(state, row); });
        var all;
        if (state.sort) {
            all = filtered ? ordered : ordered.concat(state.rows.filter(function(row) { return row.summary; }));
        } else {
            all = state.rows.filter(function(row) { return row.summary ? !filtered : _passes(state, row); });
        }
        var pages = Math.max(1, Math.ceil(all.length / state.pageRows));
        state.page = Math.min(Math.max(state.page, 1), pages);
        var first = (state.page - 1) * state.pageRows;
        var onPage = all.slice(first, first + state.pageRows);

        state.rows.forEach(function(row) { _setHidden(row.el, onPage.indexOf(row) === -1); });
        var y = state.rowsY;
        onPage.forEach(function(row, slot) {
            var previous = slot > 0 ? onPage[slot - 1] : null;
            if (previous && row.summary && !previous.summary) y += state.gap;
            var shift = y - row.y;
            if (shift) row.el.setAttribute('transform', 'translate(0 ' + shift + ')');
            else row.el.removeAttribute('transform');
            y += row.height;
            if (row.stripe) _setHidden(row.stripe, slot % 2 === 0);
            if (row.rule) {
                var next = onPage[slot + 1];
                _setHidden(row.rule, !next || next.summary);
            }
        });
        ordered.forEach(function(row, i) {
            if (row.number) row.number.textContent = String(i + 1);
        });
        _markAffixes(state, onPage);
        _layoutPager(state, all.length, pages, first, onPage.length);
        _markHeaders(state);
    }

    /*{# The engine draws each anchored affix on every row, tagged with a bitmask of the #}*/
    /*{# (symbol anchor?, magnitude anchor?) slots that paint it (bit 2*symbol + magnitude). #}*/
    /*{# A column's $ and % anchor on its first non-null data row of the page; a #}*/
    /*{# shared-scale column's M anchors on its first nonzero row, else the first. A #}*/
    /*{# summary row paints its own, never anchors. #}*/
    function _markAffixes(state, onPage) {
        var dataRows = onPage.filter(function(row) { return !row.summary; });
        var first = dataRows[0];
        var symbolRows = {};
        function symbolRow(col) {
            if (!(col in symbolRows)) {
                symbolRows[col] = dataRows.find(function(row) {
                    return _sortKey(state.types[col], row, col) !== null;
                });
            }
            return symbolRows[col];
        }
        var magnitudeRows = {};
        function magnitudeRow(col) {
            if (!(col in magnitudeRows)) {
                magnitudeRows[col] = dataRows.find(function(row) {
                    var cell = row.cells[col];
                    var value = cell && cell.key !== null && cell.key.trim() !== '' ? Number(cell.key) : NaN;
                    return isFinite(value) && value !== 0;
                }) || first;
            }
            return magnitudeRows[col];
        }
        state.rows.forEach(function(row) {
            row.affixes.forEach(function(affix) {
                var slot = 2 * (row === symbolRow(affix.col) ? 1 : 0) + (row === magnitudeRow(affix.col) ? 1 : 0);
                affix.el.classList.toggle('dbt-table-affix-shown', ((affix.mask >> slot) & 1) === 1);
            });
        });
    }

    /*{# Unfiltered, the engine's per-page pager is exact; filtered (or when the #}*/
    /*{# engine drew no per-page pagers) the page count is the runtime's to know, #}*/
    /*{# so the compact one is relabeled and retargeted. #}*/
    function _layoutPager(state, total, pages, first, shown) {
        var useCompact = _filterActive(state) || !state.perPagePagers;
        _children(state.table, 'data-dbt-pager').forEach(function(pager) {
            var id = pager.getAttribute('data-dbt-pager');
            var visible = useCompact ? id === 'compact' && pages > 1 : id === String(state.page);
            _setHidden(pager, !visible);
        });
        var compact = state.table.querySelector('[data-dbt-pager="compact"]');
        if (!compact || !useCompact) return;
        var label = compact.querySelector('[data-dbt-pager-label]');
        if (label) {
            var values = { first: first + 1, last: first + shown, total: total };
            label.textContent = _word(state.table, 'page_range').replace(/\{(\w+)\}/g, function(_, name) {
                return values[name];
            });
        }
        [['prev', state.page > 1], ['next', state.page < pages]].forEach(function(item) {
            var target = _requireHook(compact, '[data-dbt-page-target="' + item[0] + '"]');
            var glyph = _requireHook(compact, '[data-paginator-role="' + item[0] + '"]');
            _setHidden(target, !item[1]);
            glyph.setAttribute('fill', glyph.getAttribute(item[1] ? 'data-dbt-fill-enabled' : 'data-dbt-fill-disabled'));
        });
    }

    /*{# A mark is a tspan appended to the header text: it inherits the header's #}*/
    /*{# ink and needs no geometry. The filter dot is set smaller so it reads as a #}*/
    /*{# badge, not a second glyph as loud as the label. #}*/
    function _setMark(header, kind, text) {
        var mark = header.querySelector('[data-dbt-mark="' + kind + '"]');
        if (!text) {
            if (mark) mark.remove();
            return;
        }
        if (!mark) {
            mark = document.createElementNS(SVG_NS, 'tspan');
            mark.setAttribute('data-dbt-mark', kind);
            if (kind === 'filter') mark.setAttribute('font-size', '0.6em');
            header.appendChild(mark);
        }
        mark.textContent = text;
    }

    function _markHeaders(state) {
        Object.keys(state.headers).forEach(function(col) {
            var sorted = state.sort && state.sort.col === col;
            _setMark(state.headers[col], 'sort', sorted ? (state.sort.dir > 0 ? ' \u25B2' : ' \u25BC') : '');
            _setMark(state.headers[col], 'filter', state.filters[col] ? ' \u25CF' : '');
        });
    }

    /*{# ── Header menu ─────────────────────────────────────────────────────── #}*/

    function _closeMenu() {
        if (!_menu) return;
        _menu.el.remove();
        _menu.header.setAttribute('aria-expanded', 'false');
        _menu = null;
    }

    function _distinctValues(state, col) {
        var seen = Object.create(null);
        var firstRows = [];
        state.rows.forEach(function(row) {
            if (row.summary || !row.cells[col] || seen[row.cells[col].text]) return;
            seen[row.cells[col].text] = true;
            firstRows.push(row);
        });
        return _sorted(state, firstRows, col, 1).map(function(row) { return row.cells[col].text; });
    }

    function _menuItem(label, action, selected) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'dbt-select-option';
        item.setAttribute('role', 'menuitem');
        item.setAttribute('data-action', action);
        item.setAttribute('aria-selected', selected ? 'true' : 'false');
        item.textContent = label;
        return item;
    }

    function _buildChecklist(state, col, values) {
        var wrap = document.createElement('div');
        wrap.className = 'dbt-table-menu-filter';
        var search = document.createElement('input');
        search.type = 'search';
        search.className = 'dbt-table-menu-search';
        search.placeholder = _word(state.table, 'search');
        search.setAttribute('aria-label', _word(state.table, 'search_values'));
        var list = document.createElement('div');
        list.className = 'dbt-table-menu-values';
        var checks = values.map(function(value) {
            var row = document.createElement('label');
            row.className = 'dbt-select-option dbt-table-menu-value';
            var box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = !state.filters[col] || state.filters[col].has(value);
            box.setAttribute('data-value', value);
            row.appendChild(box);
            row.appendChild(document.createTextNode(' ' + value));
            list.appendChild(row);
            return box;
        });
        search.addEventListener('input', function() {
            var needle = search.value.toLowerCase();
            checks.forEach(function(box) {
                var matches = box.getAttribute('data-value').toLowerCase().indexOf(needle) !== -1;
                box.parentNode.style.display = matches ? '' : 'none';
            });
        });
        list.addEventListener('change', function() {
            var allowed = new Set(checks.filter(function(box) { return box.checked; })
                .map(function(box) { return box.getAttribute('data-value'); }));
            if (allowed.size === values.length) delete state.filters[col];
            else state.filters[col] = allowed;
            state.page = 1;
            _layout(state);
        });
        wrap.appendChild(search);
        wrap.appendChild(list);
        return wrap;
    }

    /*{# Page-level and viewport-positioned, like the variable popovers: a fixed #}*/
    /*{# element inside the board would shrink with the board's scale. #}*/
    function _positionMenu(header, el) {
        var rect = header.getBoundingClientRect();
        var margin = 8;
        el.style.position = 'fixed';
        var box = el.getBoundingClientRect();
        var width = box.width;
        /*{# Slide up rather than run off the bottom of the viewport. #}*/
        var top = Math.min(rect.bottom + 6, window.innerHeight - box.height - margin);
        el.style.top = Math.max(margin, top) + 'px';
        var overflows = rect.left + width > window.innerWidth - margin;
        el.style.left = (overflows ? Math.max(margin, rect.right - width) : rect.left) + 'px';
    }

    function _openMenu(state, header) {
        var col = header.getAttribute('data-col');
        var el = document.createElement('div');
        el.className = 'dbt-popover dbt-table-menu dbt-popover-open';
        el.setAttribute('role', 'menu');
        var dir = state.sort && state.sort.col === col ? state.sort.dir : 0;
        el.appendChild(_menuItem(_word(state.table, 'sort_ascending'), 'asc', dir === 1));
        el.appendChild(_menuItem(_word(state.table, 'sort_descending'), 'desc', dir === -1));
        el.appendChild(_menuItem(_word(state.table, 'clear_sort'), 'clear', false));
        var values = _distinctValues(state, col);
        if (values.length > 0 && values.length <= MAX_CHECKLIST_VALUES) {
            el.appendChild(_buildChecklist(state, col, values));
        }
        el.addEventListener('click', function(event) {
            var item = event.target.closest('[data-action]');
            if (!item) return;
            var action = item.getAttribute('data-action');
            state.sort = action === 'clear' ? null : { col: col, dir: action === 'asc' ? 1 : -1 };
            state.page = 1;
            _layout(state);
            _closeMenu();
        });
        document.body.appendChild(el);
        _positionMenu(header, el);
        header.setAttribute('aria-expanded', 'true');
        _menu = { el: el, header: header };
    }

    function _toggleMenu(state, header) {
        var wasOpen = _menu && _menu.header === header;
        _closeMenu();
        if (!wasOpen) _openMenu(state, header);
    }

    /*{# ── Binding ─────────────────────────────────────────────────────────── #}*/

    function _bindTable(table) {
        if (table.hasAttribute('data-dbt-bound')) return;
        table.setAttribute('data-dbt-bound', 'true');
        var state = {
            table: table,
            rows: _children(table, 'data-dbt-row').map(_readRow),
            pageRows: _numberHook(table, 'data-dbt-page-rows'),
            rowsY: _numberHook(table, 'data-dbt-rows-y'),
            gap: _numberHook(table, 'data-dbt-summary-gap'),
            perPagePagers: _children(table, 'data-dbt-pager').some(function(pager) {
                return pager.getAttribute('data-dbt-pager') !== 'compact';
            }),
            headers: {},
            types: {},
            sort: null,
            filters: {},
            page: _numberHook(table, 'data-dbt-page'),
        };
        table.querySelectorAll('[data-dbt-sortable]').forEach(function(header) {
            var col = header.getAttribute('data-col');
            var type = header.getAttribute('data-dbt-sort-type');
            if (type !== 'number' && type !== 'text') {
                throw new Error('table runtime: missing or unknown data-dbt-sort-type on column ' + col);
            }
            state.headers[col] = header;
            state.types[col] = type;
            header.setAttribute('tabindex', '0');
            header.setAttribute('role', 'button');
            header.setAttribute('aria-haspopup', 'menu');
            header.setAttribute('aria-expanded', 'false');
        });
        _markAffixes(state, state.rows.filter(function(row) { return row.el.getAttribute('display') !== 'none'; }));
        /*{# Compact-only pagers open unlabeled: size them to the open page now. #}*/
        if (!state.perPagePagers && _children(table, 'data-dbt-pager').length) _layout(state);
        table.addEventListener('click', function(event) {
            var header = event.target.closest('[data-dbt-sortable]');
            if (header) {
                _toggleMenu(state, header);
                return;
            }
            var target = event.target.closest('[data-dbt-page-target]');
            if (!target) return;
            var value = target.getAttribute('data-dbt-page-target');
            if (value !== 'prev' && value !== 'next' && !isFinite(Number(value))) {
                throw new Error('table runtime: bad data-dbt-page-target ' + value);
            }
            state.page = value === 'prev' ? state.page - 1
                : value === 'next' ? state.page + 1 : Number(value);
            _layout(state);
        });
        table.addEventListener('keydown', function(event) {
            var header = event.target.closest('[data-dbt-sortable]');
            if (header && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                _toggleMenu(state, header);
            }
        });
    }

    /*{# Idempotent, like the variable runtime's mount: a host calls it after #}*/
    /*{# every board swap, and a bound table is left alone. #}*/
    function mount(root) {
        var scope = root || document;
        if (_menu && !document.contains(_menu.header)) _closeMenu();
        var tables = Array.prototype.slice.call(scope.querySelectorAll('[data-dbt-table]'));
        if (scope.matches && scope.matches('[data-dbt-table]')) tables.unshift(scope);
        tables.forEach(_bindTable);
    }

    /*{# The header toggles its own menu; any other press outside it dismisses it. #}*/
    document.addEventListener('click', function(event) {
        if (!_menu || _menu.el.contains(event.target)) return;
        if (event.target.closest('[data-dbt-sortable]') === _menu.header) return;
        _closeMenu();
    });
    document.addEventListener('keydown', function(event) {
        if (event.key !== 'Escape' || !_menu) return;
        var header = _menu.header;
        _closeMenu();
        header.focus();
    });
    window.addEventListener('scroll', function(event) {
        if (_menu && !_menu.el.contains(event.target)) _closeMenu();
    }, true);
    window.addEventListener('resize', _closeMenu);

    window.dbtTableRuntime = { mount: mount };
})();
