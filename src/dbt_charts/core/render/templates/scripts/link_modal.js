/*{# The host side of `link: {open: modal}`: one dialog holding the target
   board in an iframe. Modals never nest: a board link clicked inside replaces
   the modal's board, and Back steps through the boards visited there.

   Each host supplies `load(href)` -> the iframe src (or a promise of it) for a
   root-relative board href: a `?_embed` URL on a server, a rendered blob URL
   in the playground. The board inside reports its clicks with the messages a
   framed board already sends its host (variables.js). #}*/
(function() {
    if (window.dbtLinkModal) return;

    /*{# The board inside reads this name to know it sits in a modal; a blob #}*/
    /*{# URL frame has no query string to carry that. #}*/
    var FRAME_NAME = 'dbt-link-modal';

    var DRAG_PATH = 'M11 18c0 1.1-.9 2-2 2s-2-.9-2-2 .9-2 2-2 2 .9 2 2zm-2-8c-1.1 0-2 .9-2 '
        + '2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0-6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm6 '
        + '4c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 '
        + '2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z';
    var BACK_PATH = 'M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z';
    var NEW_TAB_PATH = 'M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 '
        + '2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z';
    var CLOSE_PATH = 'M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 '
        + '6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z';
    /*{# The dialog fits the board it shows (fit() below), capped at this share #}*/
    /*{# of the window; a board past the cap scrolls inside its frame. #}*/
    var MAX_WIDTH = 0.92;
    var MAX_HEIGHT = 0.86;
    /*{# A preview stays small: the board scaled down to this width, at most #}*/
    /*{# this tall (it scrolls past that); "open in new tab" shows it full size. #}*/
    var PREVIEW_WIDTH = 480;
    var PREVIEW_HEIGHT = 360;
    /*{# Gap between the mark and the preview, and the window edge inset. #}*/
    var PREVIEW_GAP = 8;
    /*{# The smallest a user can resize the dialog to. #}*/
    var MIN_WIDTH = 200;
    var MIN_HEIGHT = 120;
    /*{# Resize zones along the edges and corners, inside the dialog. #}*/
    var EDGES = {
        n: 'top:0;left:10px;right:10px;height:5px;cursor:ns-resize',
        s: 'bottom:0;left:10px;right:10px;height:5px;cursor:ns-resize',
        e: 'right:0;top:10px;bottom:10px;width:5px;cursor:ew-resize',
        w: 'left:0;top:10px;bottom:10px;width:5px;cursor:ew-resize',
        nw: 'top:0;left:0;width:10px;height:10px;cursor:nwse-resize',
        se: 'bottom:0;right:0;width:10px;height:10px;cursor:nwse-resize',
        ne: 'top:0;right:0;width:10px;height:10px;cursor:nesw-resize',
        sw: 'bottom:0;left:0;width:10px;height:10px;cursor:nesw-resize',
    };
    /*{# At or below this width the dialog is the whole screen. #}*/
    var FULL_SCREEN_WIDTH = 600;
    /*{# Until its board is sized the dialog stays hidden, so it grows from the #}*/
    /*{# clicked mark into its final box; a board that never sizes shows anyway. #}*/
    var REVEAL_TIMEOUT_MS = 1200;
    var REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');
    /*{# margin: auto restates the UA centering a host's CSS reset removes. #}*/
    var STYLE = ''
        + '.dbt-link-modal{width:min(720px,92vw);height:min(480px,86vh);max-width:92vw;'
        + 'max-height:86vh;margin:auto;padding:0;border:0;transform-origin:0 0;'
        + 'border-radius:10px;box-shadow:0 12px 48px rgba(0,0,0,.35);overflow:hidden;}'
        /*{# !important beats the inline fitted size when a window narrows or #}*/
        /*{# a tablet rotates. A full-screen dialog has nowhere to be dragged. #}*/
        + '@media (max-width:' + FULL_SCREEN_WIDTH + 'px){.dbt-link-modal{width:100vw!important;'
        + 'height:100vh!important;left:0!important;top:0!important;max-width:none;'
        + 'max-height:none;border-radius:0;}'
        + '.dbt-link-modal [data-modal-drag],.dbt-link-modal-edge{display:none;}'
        + '.dbt-link-modal-bar{cursor:default;}}'
        + '.dbt-link-modal::backdrop{background:rgba(0,0,0,.4);}'
        + '.dbt-link-modal-pending,.dbt-link-modal-pending::backdrop{opacity:0;}'
        /*{# The bar sits over the scroller's top padding, so the board scrolls #}*/
        /*{# up behind it; it stops short of the scrollbar. #}*/
        + '.dbt-link-modal-bar{position:absolute;top:0;left:0;'
        + 'right:var(--dbt-link-modal-gutter,0px);z-index:1;height:36px;box-sizing:border-box;'
        + 'display:flex;align-items:center;gap:2px;padding:0 4px;'
        + 'background:rgba(255,255,255,.88);-webkit-backdrop-filter:blur(6px);'
        + 'backdrop-filter:blur(6px);border-bottom:1px solid rgba(0,0,0,.12);'
        + 'cursor:grab;touch-action:none;}'
        + '.dbt-link-modal-bar:active{cursor:grabbing;}'
        + '.dbt-link-modal-bar-fill{flex:1;}'
        + '.dbt-link-modal-edge{position:absolute;z-index:2;touch-action:none;}'
        + '.dbt-link-modal-body{box-sizing:border-box;width:100%;height:100%;padding-top:36px;'
        + 'overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;}'
        + '.dbt-link-modal iframe{width:100%;height:100%;border:0;display:block;}'
        + '.dbt-link-modal-btn{flex:none;width:28px;height:28px;padding:0;border:0;'
        + 'border-radius:50%;background:transparent;color:#5f6368;display:flex;'
        + 'align-items:center;justify-content:center;cursor:pointer;}'
        + '.dbt-link-modal-btn svg{width:18px;height:18px;}'
        + '.dbt-link-modal [data-modal-drag]{cursor:inherit;}'
        + '.dbt-link-modal-btn:hover:not(:disabled):not([data-modal-drag]){'
        + 'background:rgba(60,64,67,.1);}'
        + '.dbt-link-modal-btn:active:not(:disabled):not([data-modal-drag]){'
        + 'background:rgba(60,64,67,.18);}'
        + '.dbt-link-modal-btn:focus-visible{outline:2px solid #1a73e8;outline-offset:1px;}'
        + '.dbt-link-modal [data-modal-back]:disabled{display:none;}'
        + '.dbt-link-modal.dbt-link-preview{box-sizing:border-box;'
        + 'position:fixed;margin:0;inset:auto;z-index:2147483647;'
        /*{# A ring, not a border: a border would eat into the fitted size. #}*/
        + 'max-width:none;max-height:none;'
        + 'box-shadow:0 0 0 1px rgba(0,0,0,.08),0 8px 28px rgba(0,0,0,.28);}'
        + '.dbt-link-preview .dbt-link-modal-bar{height:28px;padding:0 2px;}'
        + '.dbt-link-preview .dbt-link-modal-body{padding-top:28px;}'
        + '.dbt-link-preview .dbt-link-modal-btn{width:24px;height:24px;}'
        + '.dbt-link-preview .dbt-link-modal-btn svg{width:16px;height:16px;}';

    function button(label, path, data) {
        return '<button type="button" class="dbt-link-modal-btn" ' + data + ' aria-label="'
            + label + '" title="' + label + '">' + icon(path) + '</button>';
    }

    function icon(path) {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
            + '<path fill="currentColor" d="' + path + '"/></svg>';
    }

    /*{# Encoded as variables.js commits to a URL: a list repeats its key. #}*/
    /*{# Params the change does not name (a hidden variable the link set) stay. #}*/
    function withVariables(href, variables) {
        var path = href.split('#')[0].split('?')[0];
        var params = new URLSearchParams(href.split('#')[0].slice(path.length + 1));
        Object.keys(variables).forEach(function(name) {
            var value = variables[name];
            params.delete(name);
            if (value === null || value === undefined) return;
            if (!Array.isArray(value)) {
                params.set(name, String(value));
            } else if (!value.length) {
                params.append(name, '');
            } else {
                value.forEach(function(member) { params.append(name, String(member)); });
            }
        });
        var query = params.toString();
        return path + (query ? '?' + query : '');
    }

    /*{# `pageUrl(href)`: the full page a board href opens in a new tab. #}*/
    function create(load, pageUrl) {
        var dialog = null;
        var frame = null;
        var body = null;
        var bar = null;
        var back = null;
        var visited = [];
        var pending = 0;
        /*{# Size of the last fit; null until the first fit after opening. #}*/
        var fitted = null;
        /*{# The clicked mark's box, in this page's viewport, for this open. #}*/
        var origin = null;
        var revealed = true;
        var revealTimer = 0;
        var closing = false;
        /*{# 'modal' or 'preview' for this open. #}*/
        var mode = 'modal';
        /*{# Bumped per open, so a close animation still running from the #}*/
        /*{# previous open cannot close the new one. #}*/
        var opens = 0;
        /*{# Dragged by the user this open: it stays put, no re-anchoring. #}*/
        var moved = false;
        /*{# Resized by the user this open: the board follows the dialog's #}*/
        /*{# size instead of setting it. #}*/
        var resized = false;
        /*{# The shown board's natural size and frame padding, once known. #}*/
        var natural = null;

        /*{# Below the mark when it fits, else above; clamped into the window. #}*/
        function place() {
            if (mode !== 'preview' || !origin || moved) return;
            /*{# Layout size, not the bounding box: a refit can land mid-grow, #}*/
            /*{# while the animation's transform still shrinks the box. #}*/
            var width = dialog.offsetWidth;
            var height = dialog.offsetHeight;
            var left = Math.min(
                Math.max(origin.left, PREVIEW_GAP), window.innerWidth - width - PREVIEW_GAP);
            var below = origin.top + origin.height + PREVIEW_GAP;
            var roomBelow = window.innerHeight - PREVIEW_GAP - below;
            var roomAbove = origin.top - 2 * PREVIEW_GAP;
            /*{# Below if it fits, else above if that fits, else the roomier #}*/
            /*{# side, clamped into the window (it may cover the mark). #}*/
            var useBelow = height <= roomBelow || (height > roomAbove && roomBelow >= roomAbove);
            var top = useBelow ? below : origin.top - PREVIEW_GAP - height;
            top = Math.min(Math.max(PREVIEW_GAP, top), window.innerHeight - PREVIEW_GAP - height);
            dialog.style.left = Math.max(PREVIEW_GAP, left) + 'px';
            dialog.style.top = top + 'px';
        }

        /*{# Animate between the clicked mark's box and the dialog's own. #}*/
        function morph(collapse, done) {
            var box = dialog.getBoundingClientRect();
            if (!origin || REDUCED_MOTION.matches || !box.width || !box.height) {
                if (done) done();
                return;
            }
            var mark = 'translate(' + (origin.left - box.left) + 'px,'
                + (origin.top - box.top) + 'px) scale(' + (origin.width / box.width) + ','
                + (origin.height / box.height) + ')';
            var frames = [{ transform: mark, opacity: 0 }, { transform: 'none', opacity: 1 }];
            var fades = [{ opacity: 0 }, { opacity: 1 }];
            if (collapse) { frames.reverse(); fades.reverse(); }
            var timing = { duration: collapse ? 180 : 260, easing: 'cubic-bezier(.2,0,0,1)' };
            dialog.animate(fades, Object.assign({ pseudoElement: '::backdrop' }, timing));
            var growth = dialog.animate(frames, timing);
            if (done) growth.onfinish = done;
        }

        function reveal() {
            if (revealed) return;
            revealed = true;
            clearTimeout(revealTimer);
            dialog.classList.remove('dbt-link-modal-pending');
            morph(false);
        }

        /*{# Closes and clears now, not in the 'close' event: that event is #}*/
        /*{# queued, so it would land after an open that follows at once. #}*/
        function dismiss() {
            visited = [];
            fitted = null;
            clearTimeout(revealTimer);
            revealed = true;
            dialog.classList.remove('dbt-link-modal-pending');
            dialog.style.width = '';
            dialog.style.height = '';
            dialog.style.left = '';
            dialog.style.top = '';
            dialog.style.margin = '';
            dialog.style.inset = '';
            dialog.style.maxWidth = '';
            dialog.style.maxHeight = '';
            moved = false;
            resized = false;
            natural = null;
            dialog.style.removeProperty('--dbt-link-modal-gutter');
            pending++;
            setFrame(null);
            dialog.close();
        }

        function close() {
            if (!dialog.open || closing) return;
            closing = true;
            var closingOpen = opens;
            morph(true, function() {
                closing = false;
                if (closingOpen === opens) dismiss();
            });
        }

        /*{# The board's natural size (its viewBox) plus the padding its frame #}*/
        /*{# puts around it. Until the user resizes it, the dialog fits that. #}*/
        function fit(doc) {
            var board = doc.querySelector('[data-dbt-board] svg[data-dbt-page-background]');
            if (!board) return;
            var box = (board.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
            if (window.innerWidth <= FULL_SCREEN_WIDTH || !(box[2] > 0 && box[3] > 0)) {
                reveal();
                return;
            }
            var at = board.closest('[data-dbt-board]').getBoundingClientRect();
            natural = {
                width: box[2] + 2 * at.left,
                height: box[3] + 2 * at.top,
                padX: 2 * at.left,
                padY: 2 * at.top,
                ratio: box[3] / box[2],
            };
            if (!resized) size();
            layout();
            place();
            reveal();
        }

        /*{# A preview shows the board scaled down to its width, capped in #}*/
        /*{# height. A modal shows it at full size, capped to the window; its #}*/
        /*{# first fit after opening is exact, later ones only grow, so a drill #}*/
        /*{# never jumps it smaller. #}*/
        function size() {
            var width;
            var height;
            if (mode === 'preview') {
                var scale = Math.min(1, PREVIEW_WIDTH / natural.width);
                width = natural.width * scale;
                height = Math.min(natural.height * scale, PREVIEW_HEIGHT) + bar.offsetHeight;
            } else {
                width = Math.min(natural.width, window.innerWidth * MAX_WIDTH);
                height = Math.min(
                    natural.ratio * (width - natural.padX) + natural.padY + bar.offsetHeight,
                    window.innerHeight * MAX_HEIGHT);
                if (fitted) {
                    width = Math.max(width, fitted.width);
                    height = Math.max(height, fitted.height);
                }
                fitted = { width: width, height: height };
            }
            dialog.style.width = Math.ceil(width) + 'px';
            dialog.style.height = Math.ceil(height) + 'px';
        }

        /*{# The board fills the dialog's width and the body scrolls its #}*/
        /*{# height behind the bar. A preview zooms the board at its natural #}*/
        /*{# size (zoom, unlike a transform, shrinks the layout too); a modal #}*/
        /*{# board scales itself to its frame's width. Twice: the first pass #}*/
        /*{# may bring in a scrollbar that narrows the board. #}*/
        function layout() {
            if (!natural) return;
            for (var pass = 0; pass < 2; pass++) {
                if (mode === 'preview') {
                    frame.style.width = natural.width + 'px';
                    frame.style.height = natural.height + 'px';
                    frame.style.zoom = body.clientWidth / natural.width;
                } else {
                    frame.style.height = Math.ceil(
                        natural.ratio * (frame.clientWidth - natural.padX) + natural.padY) + 'px';
                }
            }
        }

        /*{# The width of the body's scrollbar once laid out at its fitted #}*/
        /*{# size (0 for overlay scrollbars), which the bar stops short of. #}*/
        function gutter(shown) {
            requestAnimationFrame(function() {
                if (shown !== frame) return;
                var width = body.offsetWidth - body.clientWidth;
                dialog.style.setProperty('--dbt-link-modal-gutter', width + 'px');
            });
        }

        /*{# Whether `el` or an ancestor below `stop` can still scroll by the #}*/
        /*{# wheel delta; a document's root scrolls whatever its overflow says. #}*/
        function room(el, stop, dx, dy) {
            for (; el && el !== stop; el = el.parentElement) {
                var style = el.ownerDocument.defaultView.getComputedStyle(el);
                var root = el === el.ownerDocument.scrollingElement;
                if (dy && (root || /auto|scroll/.test(style.overflowY))
                    && (dy < 0 ? el.scrollTop > 0
                        : el.scrollTop + el.clientHeight < el.scrollHeight - 1)) return true;
                if (dx && (root || /auto|scroll/.test(style.overflowX))
                    && (dx < 0 ? el.scrollLeft > 0
                        : el.scrollLeft + el.clientWidth < el.scrollWidth - 1)) return true;
            }
            return false;
        }

        /*{# A wheel over the dialog never scrolls the page behind it: what #}*/
        /*{# nothing inside can take is cancelled rather than chained out. #}*/
        /*{# Over the bar, which sits outside the scroller, the wheel scrolls #}*/
        /*{# the board as if it were over it. #}*/
        function containWheel(event) {
            var target = event.target;
            if (target.ownerDocument === document && !body.contains(target)) {
                event.preventDefault();
                var line = event.deltaMode === 1 ? 16 : 1;
                body.scrollBy(event.deltaX * line, event.deltaY * line);
                return;
            }
            var free = room(target, target.ownerDocument === document ? dialog : null,
                event.deltaX, event.deltaY) || room(body, dialog, event.deltaX, event.deltaY);
            if (!free) event.preventDefault();
        }

        /*{# A host may draw its board after load (Cloud renders it async), so #}*/
        /*{# every change to the frame's content refits. #}*/
        function watch(next) {
            next.addEventListener('load', function() {
                var doc = next.contentDocument;
                if (!doc || !doc.body) return;
                doc.addEventListener('wheel', containWheel, { passive: false });
                fit(doc);
                gutter(next);
                new MutationObserver(function() {
                    if (next !== frame) return;
                    fit(doc);
                    gutter(next);
                })
                    .observe(doc.body, { childList: true, subtree: true });
            });
        }

        /*{# A fresh frame per board: its first load replaces about:blank, #}*/
        /*{# where reassigning src would add a host history entry each time. #}*/
        function setFrame(src) {
            var next = document.createElement('iframe');
            next.name = FRAME_NAME;
            watch(next);
            if (src) next.src = src;
            if (frame) frame.replaceWith(next); else body.appendChild(next);
            frame = next;
        }

        function show() {
            back.disabled = visited.length < 2;
            var href = visited[visited.length - 1];
            var ticket = ++pending;
            Promise.resolve(load(href)).then(function(src) {
                if (ticket === pending && dialog.open) setFrame(src);
            }).catch(function(error) {
                console.error('link modal: could not load ' + href, error);
            });
        }

        function clamp(value, low, high) {
            return Math.max(low, Math.min(value, high));
        }

        /*{# Dragging the bar moves the dialog; dragging an edge or corner #}*/
        /*{# (`edges`, e.g. 'se') resizes it. Either stays inside the window. #}*/
        /*{# The first one pins the dialog where it is: margin, inset and the #}*/
        /*{# size caps no longer center, anchor or cap it. #}*/
        function grab(handle, edges) {
            handle.addEventListener('pointerdown', function(event) {
                if (event.button !== 0 || window.innerWidth <= FULL_SCREEN_WIDTH) return;
                if (!edges && event.target.closest('button')) return;
                event.preventDefault();
                var start = dialog.getBoundingClientRect();
                var x = event.clientX;
                var y = event.clientY;
                dialog.style.margin = '0';
                dialog.style.inset = 'auto';
                dialog.style.maxWidth = 'none';
                dialog.style.maxHeight = 'none';
                dialog.style.left = start.left + 'px';
                dialog.style.top = start.top + 'px';
                moved = true;
                if (edges) resized = true;
                /*{# The frame would swallow the pointer once it crosses it. #}*/
                frame.style.pointerEvents = 'none';
                handle.setPointerCapture(event.pointerId);
                function follow(e) {
                    var dx = e.clientX - x;
                    var dy = e.clientY - y;
                    if (!edges) {
                        dialog.style.left = clamp(start.left + dx, 0, window.innerWidth - start.width) + 'px';
                        dialog.style.top = clamp(start.top + dy, 0, window.innerHeight - start.height) + 'px';
                        return;
                    }
                    var left = start.left;
                    var top = start.top;
                    var right = start.right;
                    var bottom = start.bottom;
                    if (edges.indexOf('w') >= 0) left = clamp(left + dx, 0, right - MIN_WIDTH);
                    if (edges.indexOf('e') >= 0) right = clamp(right + dx, left + MIN_WIDTH, window.innerWidth);
                    if (edges.indexOf('n') >= 0) top = clamp(top + dy, 0, bottom - MIN_HEIGHT);
                    if (edges.indexOf('s') >= 0) bottom = clamp(bottom + dy, top + MIN_HEIGHT, window.innerHeight);
                    dialog.style.left = left + 'px';
                    dialog.style.top = top + 'px';
                    dialog.style.width = (right - left) + 'px';
                    dialog.style.height = (bottom - top) + 'px';
                    layout();
                }
                function release() {
                    handle.removeEventListener('pointermove', follow);
                    handle.removeEventListener('pointerup', release);
                    handle.removeEventListener('pointercancel', release);
                    frame.style.pointerEvents = '';
                    gutter(frame);
                }
                handle.addEventListener('pointermove', follow);
                handle.addEventListener('pointerup', release);
                handle.addEventListener('pointercancel', release);
            });
        }

        function build() {
            var style = document.createElement('style');
            style.textContent = STYLE;
            document.head.appendChild(style);
            dialog = document.createElement('dialog');
            dialog.className = 'dbt-link-modal';
            dialog.innerHTML = '<div class="dbt-link-modal-bar">'
                + '<span class="dbt-link-modal-btn" data-modal-drag title="Move">'
                + icon(DRAG_PATH) + '</span>'
                + button('Back', BACK_PATH, 'data-modal-back')
                + '<span class="dbt-link-modal-bar-fill"></span>'
                + button('Open in new tab', NEW_TAB_PATH, 'data-modal-new-tab')
                + button('Close', CLOSE_PATH, 'data-modal-close')
                + '</div><div class="dbt-link-modal-body"></div>'
                + Object.keys(EDGES).map(function(edges) {
                    return '<div class="dbt-link-modal-edge" data-modal-edge="' + edges
                        + '" style="' + EDGES[edges] + '"></div>';
                }).join('');
            bar = dialog.querySelector('.dbt-link-modal-bar');
            body = dialog.querySelector('.dbt-link-modal-body');
            back = dialog.querySelector('[data-modal-back]');
            var closeButton = dialog.querySelector('[data-modal-close]');
            grab(bar, '');
            Array.prototype.forEach.call(dialog.querySelectorAll('[data-modal-edge]'), function(edge) {
                grab(edge, edge.getAttribute('data-modal-edge'));
            });
            dialog.addEventListener('wheel', containWheel, { passive: false });
            dialog.querySelector('[data-modal-new-tab]').addEventListener('click', function() {
                window.open(pageUrl(visited[visited.length - 1]), '_blank', 'noopener,noreferrer');
                close();
            });
            setFrame(null);
            back.addEventListener('click', function() {
                visited.pop();
                show();
            });
            closeButton.addEventListener('click', close);
            dialog.addEventListener('click', function(event) {
                if (event.target === dialog) close();
            });
            /*{# Escape on the host page: shrink back like every other close. #}*/
            dialog.addEventListener('cancel', function(event) {
                event.preventDefault();
                close();
            });
            document.body.appendChild(dialog);
            /*{# A moved dialog stays inside a window that shrinks under it. #}*/
            window.addEventListener('resize', function() {
                if (!dialog.open || !moved) return;
                dialog.style.left = clamp(dialog.offsetLeft, 0,
                    Math.max(0, window.innerWidth - dialog.offsetWidth)) + 'px';
                dialog.style.top = clamp(dialog.offsetTop, 0,
                    Math.max(0, window.innerHeight - dialog.offsetHeight)) + 'px';
            });
            /*{# A preview closes on a press outside it, Escape, or a page #}*/
            /*{# scroll that would leave it detached from its mark. #}*/
            document.addEventListener('pointerdown', function(event) {
                if (mode === 'preview' && dialog.open && !dialog.contains(event.target)) close();
            }, true);
            document.addEventListener('keydown', function(event) {
                if (mode === 'preview' && dialog.open && event.key === 'Escape') close();
            });
            /*{# Capture sees every scroll, the preview's own body included. #}*/
            window.addEventListener('scroll', function(event) {
                if (mode === 'preview' && dialog.open && !dialog.contains(event.target)) close();
            }, true);
        }

        window.addEventListener('message', function(event) {
            if (!dialog || !dialog.open || event.source !== frame.contentWindow) return;
            var data = event.data;
            if (!data) return;
            if (data.type === 'dbt-modal-escape') {
                close();
            } else if (data.type === 'dbt-board-navigate' || data.type === 'dbt-board-open-modal') {
                visited.push(data.href);
                show();
            } else if (data.type === 'dbt-board-open-tab') {
                window.open(pageUrl(data.href), '_blank', 'noopener,noreferrer');
            } else if (data.type === 'dbt-variable-change') {
                /*{# A filter is not a drill: it replaces the current entry. #}*/
                visited[visited.length - 1] = withVariables(visited[visited.length - 1], data.variables);
                show();
            }
        });

        return {
            /*{# `from`: the clicked mark's box in this page's viewport, if known. #}*/
            /*{# `how`: 'modal' (default) or 'preview', beside the mark, which #}*/
            /*{# needs `from`. #}*/
            open: function(href, from, how) {
                if (how === 'preview' && !from) {
                    throw new Error('link preview: no clicked mark to place it beside');
                }
                if (!dialog) build();
                if (dialog.open) dismiss();
                opens++;
                closing = false;
                visited = [href];
                origin = from || null;
                mode = how === 'preview' ? 'preview' : 'modal';
                revealed = false;
                dialog.classList.toggle('dbt-link-preview', mode === 'preview');
                dialog.classList.add('dbt-link-modal-pending');
                revealTimer = setTimeout(reveal, REVEAL_TIMEOUT_MS);
                if (mode === 'preview') {
                    /*{# Non-modal: the page stays usable; light dismiss below. #}*/
                    dialog.show();
                    place();
                } else {
                    dialog.showModal();
                }
                show();
            },
            /*{# Whether a message came from the modal's board, which this #}*/
            /*{# module answers -- a host's own listener must ignore it. #}*/
            owns: function(source) { return frame !== null && source === frame.contentWindow; },
        };
    }

    window.dbtLinkModal = { FRAME_NAME: FRAME_NAME, create: create };
})();
