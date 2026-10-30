/*{# Static-export table scrolling. The engine (table family) draws a
   scrolling table whole, split into clipped panes: `data-dbt-scroll` groups
   name the axes they move on, thumbs carry their track geometry, and the
   root carries how far each axis scrolls. This only translates those groups,
   so the artifact scrolls standalone, with no host and no re-render.
   Every copy on a page initializes each table once. #}*/
(function () {
    var LINE = 40;

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
        var state = { x: 0, y: 0 };
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

        // Returns whether anything moved, so an event at an edge falls
        // through to the page.
        function scrollBy(dx, dy) {
            var x = Math.min(maxX, Math.max(0, state.x + dx));
            var y = Math.min(maxY, Math.max(0, state.y + dy));
            if (x === state.x && y === state.y) {
                return false;
            }
            state.x = x;
            state.y = y;
            paint();
            return true;
        }

        // Screen pixels to the table's own units, under any host scaling.
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
            if (scrollBy(dx * scale(), dy * scale())) {
                event.preventDefault();
            }
        }, { passive: false });

        // Drag a thumb along its track, or drag the body itself (touch).
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
            root.setPointerCapture(event.pointerId);
            event.preventDefault();
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

        // A static page toggle shows other rows: start them at the top.
        root.ownerSVGElement && root.ownerSVGElement.addEventListener('click', function (event) {
            if (event.target.closest('[data-dbt-page-target]') && state.y) {
                state.y = 0;
                paint();
            }
        });
    }

    function initializeSvg(svg) {
        svg.querySelectorAll('[data-dbt-table-scroll]').forEach(initializeTable);
    }

    function initialize() {
        var script = document.currentScript;
        var ownerSvg = script && script.closest ? script.closest('svg') : null;
        if (ownerSvg) {
            initializeSvg(ownerSvg);
            return;
        }
        document.querySelectorAll('svg').forEach(initializeSvg);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
})();
