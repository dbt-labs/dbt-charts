/*{# Table scrolling. The engine (table family) draws a scrolling table
   whole, split into clipped panes: `data-dbt-scroll` groups name the axes
   they move on, thumbs carry their track geometry, and the root carries how
   far each axis scrolls. This only translates those groups, with no
   re-render. A host ships it in controls_runtime_source() and mounts each
   board through the table runtime; a static export, which has no host,
   carries a copy inside the SVG that mounts its own board. #}*/
(function () {
    var LINE = 40;
    /*{# A wheel step at least this large is a mouse notch; a trackpad's small #}*/
    /*{# deltas apply directly and settle on a row once they stop. #}*/
    var STEP = 50;
    var NOTCH_ROWS = 3;
    var NOTCH_MS = 140;
    var SETTLE_MS = 160;
    var KEY_MS = 120;
    var PAGE_MS = 450;
    /*{# Free input is quiet this long before the offset settles on a row. #}*/
    var IDLE = 140;
    var LINGER = 1000;
    var FADE = 200;

    function num(el, name) {
        return parseFloat(el.getAttribute(name)) || 0;
    }

    function initializeTable(root) {
        if (root.__dbtTableScroll) {
            return;
        }
        root.__dbtTableScroll = true;
        var maxX = num(root, 'data-max-x');
        var maxY = num(root, 'data-max-y');
        root.setAttribute('tabindex', '0');
        /*{# A touch on the table drives the axes it scrolls; the browser keeps the other. #}*/
        root.style.touchAction = maxX && maxY ? 'none' : maxX ? 'pan-y' : 'pan-x';
        var rowsHost = root.querySelector('[data-dbt-rows]');
        var frozenHost = root.querySelector('[data-dbt-rows-frozen]');
        var edgeHost = root.querySelector('[data-dbt-rules-edge]');
        var state = { x: 0, y: 0 };
        /*{# Where an eased wheel scroll is heading; equal to state at rest. #}*/
        var target = { x: 0, y: 0 };
        var frame = 0;
        var groups = root.querySelectorAll('[data-dbt-scroll]');
        var thumbs = root.querySelectorAll('[data-dbt-scroll-thumb]');
        var shadows = root.querySelectorAll('[data-dbt-scroll-shadow]');

        /*{# The pager under the body, a page being a viewport of rows: one is drawn #}*/
        /*{# per page, and the open one states which rows the viewport shows. #}*/
        var rangeEl = null;
        var rowEnds = [];
        var svg = root.ownerSVGElement;
        if (svg) {
            svg.querySelectorAll('[data-dbt-scroll-range]').forEach(function (el) {
                if (el.getAttribute('data-dbt-scroll-for') === root.getAttribute('data-dbt-table-scroll')) rangeEl = el;
            });
        }
        if (rangeEl) {
            var endsText = rangeEl.getAttribute('data-row-ends');
            rowEnds = endsText ? endsText.split(',').map(parseFloat) : [];
        }
        var drawnTotal = rowEnds.length;
        var pageRows = rangeEl ? num(rangeEl, 'data-page-rows') : 1;
        var page = 1;
        /*{# The page a pager click is heading to, shown as current while the scroll travels. #}*/
        var pagerPage = 0;

        function pageCount() {
            return Math.max(1, Math.ceil(rowEnds.length / pageRows));
        }

        function rowTop(i) {
            var n = Math.min(i, rowEnds.length);
            return n <= 0 ? 0 : rowEnds[n - 1];
        }

        /*{# The page with the most pixels of rows on screen, a tie going to the #}*/
        /*{# earlier; at the bottom, the last. #}*/
        function pageOnScreen(viewport) {
            if (maxY > 0 && state.y >= maxY - 0.5) return pageCount();
            var best = 1;
            var bestPx = -1;
            for (var p = 1; p <= pageCount(); p++) {
                var top = rowTop((p - 1) * pageRows);
                var bottom = rowTop(p * pageRows);
                var px = Math.min(bottom, state.y + viewport) - Math.max(top, state.y);
                if (px > bestPx + 0.5) {
                    best = p;
                    bestPx = px;
                }
            }
            return best;
        }

        function nearestRow(y) {
            var best = 0;
            for (var i = 1; i <= rowEnds.length; i++) {
                if (Math.abs(rowTop(i) - y) < Math.abs(rowTop(best) - y)) best = i;
            }
            return best;
        }

        /*{# Rows are snapped only where the body scrolls rows; a table that only #}*/
        /*{# scrolls sideways has no row boundaries to rest on. #}*/
        function snapsRows() {
            return !!rangeEl && maxY > 0 && rowEnds.length > pageRows;
        }

        function snapped(y) {
            return Math.min(maxY, rowTop(nearestRow(Math.min(y, maxY))));
        }

        function setShown(el, shown) {
            if (shown) el.removeAttribute('display');
            else el.setAttribute('display', 'none');
        }

        function paintRange() {
            if (!rangeEl) return;
            var viewport = num(rangeEl, 'data-viewport');
            var last = rowEnds.filter(function (end) { return end <= state.y + viewport + 0.5; }).length;
            var gone = rowEnds.filter(function (end) { return end <= state.y + 0.5; }).length;
            var values = { first: last ? Math.min(gone + 1, last) : 0, last: last, total: rowEnds.length };
            var text = rangeEl.getAttribute('data-template').replace(/\{(\w+)\}/g, function (_, name) {
                return values[name];
            });
            var pages = pageCount();
            page = pagerPage || pageOnScreen(viewport);
            /*{# The drawn pagers count the rows the engine drew; once a filter #}*/
            /*{# changes that, the compact pager stands in. #}*/
            var compact = rowEnds.length !== drawnTotal || !rangeEl.querySelector('[data-dbt-scroll-pager="1"]');
            rangeEl.querySelectorAll('[data-dbt-scroll-pager]').forEach(function (pager) {
                var id = pager.getAttribute('data-dbt-scroll-pager');
                var shown = compact ? id === 'compact' && pages > 1 : id === String(page);
                setShown(pager, shown);
                if (!shown) return;
                var label = pager.querySelector('.dbt-paginator-label');
                if (label) label.textContent = text;
                if (id !== 'compact') return;
                [['prev', page > 1], ['next', page < pages]].forEach(function (item) {
                    var hit = pager.querySelector('[data-dbt-page-target="' + item[0] + '"]');
                    var glyph = pager.querySelector('[data-paginator-role="' + item[0] + '"]');
                    if (hit) setShown(hit, item[1]);
                    if (glyph) glyph.setAttribute('fill', glyph.getAttribute(item[1] ? 'data-dbt-fill-enabled' : 'data-dbt-fill-disabled'));
                });
            });
        }

        /*{# The scrollbar shows while the pointer is over the table, it has focus, #}*/
        /*{# or it is scrolling, and fades out after. #}*/
        var hovered = false;
        var focused = false;
        var hideTimer = 0;
        function setThumbs(opacity) {
            thumbs.forEach(function (t) { t.style.opacity = opacity; });
        }
        function settleThumbs() {
            hideTimer = 0;
            setThumbs(hovered || focused ? '1' : '0');
        }
        function showThumbs() {
            setThumbs('1');
            window.clearTimeout(hideTimer);
            hideTimer = window.setTimeout(settleThumbs, LINGER);
        }
        thumbs.forEach(function (t) {
            t.style.transition = 'opacity ' + FADE + 'ms';
        });
        setThumbs('0');
        root.addEventListener('pointerenter', function (event) {
            if (event.pointerType === 'mouse') {
                hovered = true;
                showThumbs();
            }
        });
        root.addEventListener('pointerleave', function (event) {
            if (event.pointerType === 'mouse') {
                hovered = false;
                showThumbs();
            }
        });
        root.addEventListener('focusin', function () {
            focused = true;
            showThumbs();
        });
        root.addEventListener('focusout', function () {
            focused = false;
            showThumbs();
        });

        function paint() {
            paintRange();
            groups.forEach(function (g) {
                var axes = g.getAttribute('data-dbt-scroll');
                var dx = axes.indexOf('x') >= 0 ? -state.x : 0;
                var dy = axes.indexOf('y') >= 0 ? -state.y : 0;
                g.setAttribute('transform', 'translate(' + dx + ',' + dy + ')');
            });
            thumbs.forEach(function (t) {
                var axis = t.getAttribute('data-dbt-scroll-thumb');
                var start = num(t, 'data-track-start');
                var len = num(t, 'data-track-len');
                var size = num(t, axis === 'x' ? 'width' : 'height');
                var max = axis === 'x' ? maxX : maxY;
                var offset = max > 0 ? (len - size) * (state[axis] / max) : 0;
                t.setAttribute(axis, String(start + offset));
            });
            shadows.forEach(function (s) {
                var axis = s.getAttribute('data-dbt-scroll-shadow');
                s.setAttribute('opacity', state[axis] > 0 ? '1' : '0');
            });
        }

        function stopEasing() {
            if (frame) {
                window.cancelAnimationFrame(frame);
                frame = 0;
            }
            pagerPage = 0;
        }

        function easeInOut(t) {
            return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
        }

        function reducedMotion() {
            return !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        }

        /*{# Moves to (x, y) over `ms`, jumping when motion is reduced. Returns whether #}*/
        /*{# anything moved, so an event at an edge falls through to the page. #}*/
        function animateTo(x, y, ms, shownPage) {
            x = Math.min(maxX, Math.max(0, x));
            y = Math.min(maxY, Math.max(0, y));
            if (x === target.x && y === target.y) {
                return false;
            }
            stopEasing();
            window.clearTimeout(idleTimer);
            target.x = x;
            target.y = y;
            showThumbs();
            if (!ms || reducedMotion()) {
                state.x = x;
                state.y = y;
                paint();
                return true;
            }
            pagerPage = shownPage || 0;
            var from = { x: state.x, y: state.y };
            var start = 0;
            var step = function (now) {
                start = start || now;
                var t = Math.min(1, (now - start) / ms);
                var k = easeInOut(t);
                state.x = from.x + (x - from.x) * k;
                state.y = from.y + (y - from.y) * k;
                frame = t < 1 ? window.requestAnimationFrame(step) : 0;
                if (!frame) {
                    state.x = x;
                    state.y = y;
                    pagerPage = 0;
                }
                paint();
            };
            frame = window.requestAnimationFrame(step);
            return true;
        }

        /*{# Free input (trackpad, touch, thumb) moves directly and comes to rest on #}*/
        /*{# the nearest row once it stops. #}*/
        var idleTimer = 0;
        var drag = null;
        function settleSoon() {
            window.clearTimeout(idleTimer);
            if (!snapsRows() || drag) return;
            idleTimer = window.setTimeout(function () {
                idleTimer = 0;
                animateTo(state.x, snapped(state.y), SETTLE_MS);
            }, IDLE);
        }

        /*{# Moves by a free delta. Returns whether anything moved. #}*/
        function scrollBy(dx, dy) {
            var x = Math.min(maxX, Math.max(0, state.x + dx));
            var y = Math.min(maxY, Math.max(0, state.y + dy));
            if (x === state.x && y === state.y) {
                return false;
            }
            stopEasing();
            target.x = state.x = x;
            target.y = state.y = y;
            showThumbs();
            paint();
            settleSoon();
            return true;
        }

        function scrollRows(count, ms) {
            var row = Math.min(Math.max(0, nearestRow(target.y) + count), rowEnds.length);
            return animateTo(target.x, rowTop(row), ms);
        }

        function scrollToPage(to) {
            to = Math.max(1, Math.min(pageCount(), to));
            return animateTo(target.x, rowTop((to - 1) * pageRows), PAGE_MS, to);
        }

        /*{# Screen pixels to the table's own units, under any host scaling. #}*/
        function scale() {
            var m = root.getScreenCTM();
            return m && m.a ? 1 / m.a : 1;
        }

        root.addEventListener('wheel', function (event) {
            var k = event.deltaMode === 1 ? LINE : event.deltaMode === 2 ? 400 : 1;
            var dx = event.deltaX * k;
            var dy = event.deltaY * k;
            if (event.shiftKey && !dx) {
                dx = dy;
                dy = 0;
            }
            /*{# Only the dominant axis moves, so a trackpad's sideways jitter #}*/
            /*{# never swallows the page's vertical scroll. #}*/
            if (Math.abs(dx) >= Math.abs(dy)) {
                dy = 0;
            } else {
                dx = 0;
            }
            var notch = event.deltaMode !== 0 || Math.max(Math.abs(dx), Math.abs(dy)) >= STEP;
            var moved = notch && dy && snapsRows()
                ? scrollRows(Math.sign(dy) * NOTCH_ROWS, NOTCH_MS)
                : notch && dx
                    ? animateTo(target.x + dx * scale(), target.y, NOTCH_MS)
                    : scrollBy(dx * scale(), dy * scale());
            if (moved) {
                event.preventDefault();
            }
        }, { passive: false });

        /*{# Drag a thumb along its track, or drag the body itself (touch). #}*/
        root.addEventListener('pointerdown', function (event) {
            var thumb = event.target.closest('[data-dbt-scroll-thumb]');
            if (!thumb && event.pointerType === 'mouse') {
                return;
            }
            if (!thumb && event.target.closest('a')) {
                return;
            }
            var axis = thumb ? thumb.getAttribute('data-dbt-scroll-thumb') : null;
            var ratio = 1;
            if (thumb) {
                var len = num(thumb, 'data-track-len');
                var size = num(thumb, axis === 'x' ? 'width' : 'height');
                var max = axis === 'x' ? maxX : maxY;
                ratio = len > size ? max / (len - size) : 0;
            }
            drag = { axis: axis, ratio: ratio, x: event.clientX, y: event.clientY };
            /*{# Only a thumb captures: capturing a body drag would retarget the #}*/
            /*{# click that ends a tap on a header or link. #}*/
            if (thumb) {
                root.setPointerCapture(event.pointerId);
                event.preventDefault();
            }
        });
        root.addEventListener('pointermove', function (event) {
            if (!drag) {
                return;
            }
            var s = scale();
            var dx = (event.clientX - drag.x) * s;
            var dy = (event.clientY - drag.y) * s;
            drag.x = event.clientX;
            drag.y = event.clientY;
            if (drag.axis === 'x') {
                scrollBy(dx * drag.ratio, 0);
            } else if (drag.axis === 'y') {
                scrollBy(0, dy * drag.ratio);
            } else {
                scrollBy(-dx, -dy);
            }
        });
        function endDrag() {
            drag = null;
            settleSoon();
        }
        root.addEventListener('pointerup', endDrag);
        root.addEventListener('pointercancel', endDrag);

        root.addEventListener('keydown', function (event) {
            var moved = false;
            if (snapsRows()) {
                var current = pageOnScreen(num(rangeEl, 'data-viewport'));
                var rowKeys = {
                    ArrowUp: function () { return scrollRows(-1, KEY_MS); },
                    ArrowDown: function () { return scrollRows(1, KEY_MS); },
                    PageUp: function () { return scrollToPage(current - 1); },
                    PageDown: function () { return scrollToPage(current + 1); },
                    Home: function () { return scrollToPage(1); },
                    End: function () { return scrollToPage(pageCount()); }
                };
                if (rowKeys[event.key]) moved = rowKeys[event.key]();
                else if (event.key === 'ArrowLeft') moved = scrollBy(-LINE, 0);
                else if (event.key === 'ArrowRight') moved = scrollBy(LINE, 0);
            } else {
                var bbox = root.getBBox();
                var page = Math.max(LINE, bbox.height - 2 * LINE);
                var moves = {
                    ArrowLeft: [-LINE, 0],
                    ArrowRight: [LINE, 0],
                    ArrowUp: [0, -LINE],
                    ArrowDown: [0, LINE],
                    PageUp: [0, -page],
                    PageDown: [0, page],
                    Home: [-maxX, -maxY],
                    End: [maxX, maxY]
                };
                var move = moves[event.key];
                moved = !!move && scrollBy(move[0], move[1]);
            }
            if (moved) {
                event.preventDefault();
            }
        });

        function replaceChildren(host, nodes) {
            while (host.firstChild) host.removeChild(host.firstChild);
            nodes.forEach(function (el) { host.appendChild(el); });
        }

        function rowClones() {
            return Array.prototype.map.call(rowsHost.children, function (el) {
                return el.cloneNode(true);
            });
        }

        /*{# Drops everything in `el` but its rules and the groups holding them. #}*/
        function keepRules(el) {
            if (el.hasAttribute('data-dbt-rule')) return true;
            Array.prototype.slice.call(el.children).forEach(function (child) {
                if (!keepRules(child)) el.removeChild(child);
            });
            return el.children.length > 0;
        }

        /*{# The table runtime sorts, filters and pages rows in place. The frozen #}*/
        /*{# pane and the rules over the frozen edge then mirror the rows pane, and #}*/
        /*{# the scroll range follows the rows still shown. #}*/
        function mirror() {
            if (frozenHost) replaceChildren(frozenHost, rowClones());
            if (edgeHost) replaceChildren(edgeHost, rowClones().filter(keepRules));
            if (rangeEl) {
                /*{# The rows still shown, where the runtime placed them. #}*/
                var top = num(root, 'data-rows-top');
                rowEnds = [];
                Array.prototype.forEach.call(rowsHost.children, function (el) {
                    if (el.getAttribute('display') === 'none' || el.hasAttribute('data-dbt-row-role')) return;
                    var shift = /translate\(0 (-?[\d.]+)\)/.exec(el.getAttribute('transform') || '');
                    rowEnds.push(num(el, 'data-dbt-y') + (shift ? parseFloat(shift[1]) : 0) + num(el, 'data-dbt-h') - top);
                });
                rowEnds.sort(function (a, b) { return a - b; });
                /*{# The pinned total was computed over rows a filter now hides. #}*/
                var pinned = svg.querySelector('[data-dbt-pinned="' + root.getAttribute('data-dbt-table-scroll') + '"]');
                if (pinned) setShown(pinned, rowEnds.length === drawnTotal);
            }
            if (num(root, 'data-max-y') > 0) {
                /*{# The last row lands flush: the body scrolls past every row but a page's worth. #}*/
                maxY = rangeEl ? Math.max(0, rowTop(rowEnds.length) - rowTop(pageRows)) : 0;
            }
            stopEasing();
            state.y = target.y = Math.min(target.y, maxY);
            state.x = target.x;
            paint();
        }
        var pending = false;
        if (rowsHost && window.MutationObserver) {
            /*{# The pane is also a scroll group: paint() writing its transform is not a change of rows. #}*/
            new MutationObserver(function (records) {
                if (pending) return;
                if (!records.some(function (r) { return !(r.type === 'attributes' && r.target === rowsHost); })) return;
                pending = true;
                window.requestAnimationFrame(function () {
                    pending = false;
                    mirror();
                });
            }).observe(rowsHost, { subtree: true, childList: true, attributes: true, characterData: true });
        }

        /*{# A page of the scroll pager scrolls to its first row; a static page #}*/
        /*{# toggle shows other rows, so starts them at the top. #}*/
        svg && svg.addEventListener('click', function (event) {
            var hit = event.target.closest('[data-dbt-page-target]');
            if (!hit) return;
            if (rangeEl && rangeEl.contains(hit)) {
                var value = hit.getAttribute('data-dbt-page-target');
                var to = value === 'prev' ? page - 1 : value === 'next' ? page + 1 : Number(value);
                scrollToPage(to);
                return;
            }
            if (target.y) {
                stopEasing();
                state.y = target.y = 0;
                state.x = target.x;
                paint();
            }
        });
    }

    function mount(root) {
        var scope = root || document;
        if (scope.matches && scope.matches('[data-dbt-table-scroll]')) initializeTable(scope);
        scope.querySelectorAll('[data-dbt-table-scroll]').forEach(initializeTable);
    }

    if (!window.dbtTableScroll) window.dbtTableScroll = { mount: mount };
    var script = document.currentScript;
    var ownerSvg = script && script.closest ? script.closest('svg') : null;
    if (ownerSvg) window.dbtTableScroll.mount(ownerSvg);
})();
