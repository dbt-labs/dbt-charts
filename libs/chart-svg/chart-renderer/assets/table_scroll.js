/*{# Table scrolling. The engine (table family) draws a scrolling table
   whole, split into clipped panes: `data-dbt-scroll` groups name the axes
   they move on, thumbs carry their track geometry, and the root carries how
   far each axis scrolls. This only translates those groups, with no
   re-render. A host ships it in controls_runtime_source() and mounts each
   board through the table runtime; a static export, which has no host,
   carries a copy inside the SVG that mounts its own board. #}*/
(function () {
    var LINE = 40;
    /*{# A wheel step at least this large is a mouse notch, eased over a few #}*/
    /*{# frames like a page scroll; a trackpad's small deltas apply directly. #}*/
    var STEP = 50;
    /*{# Time constant of the easing, in ms. #}*/
    var EASE = 60;

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

        function paint() {
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
        }

        function ease(last) {
            frame = window.requestAnimationFrame(function (now) {
                var f = 1 - Math.exp(-(now - (last || now - 16)) / EASE);
                var done = true;
                ['x', 'y'].forEach(function (axis) {
                    var gap = target[axis] - state[axis];
                    if (Math.abs(gap) < 0.5) {
                        state[axis] = target[axis];
                    } else {
                        state[axis] += gap * f;
                        done = false;
                    }
                });
                paint();
                frame = 0;
                if (!done) ease(now);
            });
        }

        /*{# Returns whether anything moved, so an event at an edge falls #}*/
        /*{# through to the page. An eased move counts from where it is heading. #}*/
        function scrollBy(dx, dy, smooth) {
            var x = Math.min(maxX, Math.max(0, target.x + dx));
            var y = Math.min(maxY, Math.max(0, target.y + dy));
            if (x === target.x && y === target.y) {
                return false;
            }
            target.x = x;
            target.y = y;
            if (smooth) {
                if (!frame) ease(0);
            } else {
                stopEasing();
                state.x = x;
                state.y = y;
                paint();
            }
            return true;
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
            var smooth = event.deltaMode !== 0 || Math.max(Math.abs(dx), Math.abs(dy)) >= STEP;
            if (scrollBy(dx * scale(), dy * scale(), smooth)) {
                event.preventDefault();
            }
        }, { passive: false });

        /*{# Drag a thumb along its track, or drag the body itself (touch). #}*/
        var drag = null;
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
        }
        root.addEventListener('pointerup', endDrag);
        root.addEventListener('pointercancel', endDrag);

        root.addEventListener('keydown', function (event) {
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
            if (move && scrollBy(move[0], move[1])) {
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
            if (num(root, 'data-max-y') > 0) {
                var box = rowsHost.getBBox();
                var shown = num(root, 'data-rows-bottom') - num(root, 'data-rows-top');
                maxY = Math.max(0, box.y + box.height - num(root, 'data-rows-top') - shown);
            }
            stopEasing();
            state.y = target.y = Math.min(target.y, maxY);
            state.x = target.x;
            paint();
        }
        var pending = false;
        if (rowsHost && window.MutationObserver) {
            new MutationObserver(function (records) {
                var moved = records.some(function (r) { return r.target !== rowsHost; });
                if (!moved || pending) return;
                pending = true;
                window.requestAnimationFrame(function () {
                    pending = false;
                    mirror();
                });
            }).observe(rowsHost, { subtree: true, childList: true, attributes: true, characterData: true });
        }

        /*{# A static page toggle shows other rows: start them at the top. #}*/
        root.ownerSVGElement && root.ownerSVGElement.addEventListener('click', function (event) {
            if (event.target.closest('[data-dbt-page-target]') && target.y) {
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
