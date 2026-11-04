/*{# The variable-control runtime. Ships with the host, never with the artifact.

   The server draws every control into the board SVG, so this script adds no
   markup: it binds behavior to elements that are already there. That is what
   keeps interactive surfaces out of the board's scaled coordinate system — a
   `position: fixed` element inside a transformed ancestor inherits the
   transform, so anything laid *over* a board comes out at the board's scale.

   A committed value rewrites the URL and asks for a fresh render, so controls
   only work where a server can serve one — `dct serve`, Cloud, the Playground.
   Those hosts inject this script and the controls stylesheet into their page.

   This script handles:
   - Binding the drawn controls of each board on the page (mount)
   - Variable change events (updateVariable function)
   - URL parameter updates
   - Chart loading states
   - Parent frame communication (for playground/suite embedding)
   - Variable hover highlighting

   The two transient exceptions to "no markup" — a select's popover and the
   native input a text field lifts — are page-level, native-size, and torn down
   when they close, which is why they can be HTML at all.

   Note: Chart menus are handled by Suite's JavaScript (init.js), not here. #}*/
(function() {
    /*{# Document-level listeners bind once per page. Binding is separately
       idempotent (per-control data-dbt-bound), so a host re-running this script
       after a board swap binds the new controls without double-binding these. #}*/
    var _documentWired = window.__dfVariablesInitialized === true;
    window.__dfVariablesInitialized = true;

    function setVariableIfMissing(vars, name, value) {
        if (Object.prototype.hasOwnProperty.call(vars, name)) {
            return;
        }
        vars[name] = value;
    }

    window.__dfMarkChartLoading = function(chart) {
        /*{# Namespaced: a host stylesheet may own a bare `loading` (DaisyUI's #}*/
        /*{# masks the element into its own spinner, which blanks the chart). #}*/
        var currentClass = chart.getAttribute('class') || '';
        if (currentClass.indexOf('dbt-chart-loading') === -1) {
            chart.setAttribute('class', currentClass + (currentClass ? ' ' : '') + 'dbt-chart-loading');
        }

        if (!chart.querySelector('.dbt-chart-spinner')) {
            var spinner = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            spinner.setAttribute('class', 'dbt-chart-spinner');
            /*{# Never getBBox(): this runs per chart in a loop, and each layout read forces a full-page layout. #}*/
            var centerX = parseFloat(chart.getAttribute('data-chart-width')) / 2;
            var centerY = parseFloat(chart.getAttribute('data-chart-height')) / 2;
            spinner.appendChild(loadingGlyph(centerX, centerY));
            chart.appendChild(spinner);
        }
    };

    function markDependentChartsLoading(name) {
        var charts = document.querySelectorAll('[data-var-' + name + ']');
        for (var i = 0; i < charts.length; i++) {
            window.__dfMarkChartLoading(charts[i]);
        }
    }

    /*{# A navigation that starts and never commits (Escape, the browser's #}*/
    /*{# Stop, a 204, a download response) fires neither `unload` nor #}*/
    /*{# `pageshow` -- no predicate can see it coming, only this document #}*/
    /*{# staying alive can prove it happened. One capture-phase, one-shot #}*/
    /*{# pair answers that: the next `pointerdown` or `keydown` anywhere means #}*/
    /*{# this document is still here, so whatever navigation was pending did #}*/
    /*{# not replace it. No timers -- the next interaction is the signal. #}*/
    var _navGuardArmed = false;
    function _onDocumentStillAlive() {
        /*{# Self-removing: whichever eval's `window.__dfClearChartLoading` #}*/
        /*{# is current by the time this fires, this exact pair comes off. #}*/
        _navGuardArmed = false;
        document.removeEventListener('pointerdown', _onDocumentStillAlive, true);
        document.removeEventListener('keydown', _onDocumentStillAlive, true);
        window.__dfClearChartLoading();
    }
    function _armNavigationCancelGuard() {
        if (_navGuardArmed) return;
        _navGuardArmed = true;
        document.addEventListener('pointerdown', _onDocumentStillAlive, true);
        document.addEventListener('keydown', _onDocumentStillAlive, true);
    }
    function _dropNavigationCancelGuard() {
        if (!_navGuardArmed) return;
        _navGuardArmed = false;
        document.removeEventListener('pointerdown', _onDocumentStillAlive, true);
        document.removeEventListener('keydown', _onDocumentStillAlive, true);
    }

    /*{# The loading state ends when the board is replaced -- or when the host #}*/
    /*{# knows it will not be: a failed commit keeps the old board on screen, #}*/
    /*{# and a board left dimmed and inert under a failure bar is a lie. #}*/
    window.__dfClearChartLoading = function() {
        _dropNavigationCancelGuard();
        document.querySelectorAll('.dbt-chart-loading').forEach(function(chart) {
            chart.setAttribute('class', chart.getAttribute('class')
                .split(/\s+/).filter(function(c) { return c && c !== 'dbt-chart-loading'; }).join(' '));
        });
        document.querySelectorAll('.dbt-chart-spinner').forEach(function(s) { s.remove(); });
        document.documentElement.classList.remove('dbt-page-loading');
    };

    /*{# A back/forward-cache restore resurrects the exact DOM the user #}*/
    /*{# navigated away from, mark and all -- the page never reloaded, so #}*/
    /*{# nothing else clears it. `event.persisted` is bfcache's own signal #}*/
    /*{# for that; an ordinary load fires `pageshow` too, unpersisted, and #}*/
    /*{# must not disturb a mark a script just applied. #}*/
    if (!_documentWired) window.addEventListener('pageshow', function(event) {
        if (event.persisted) window.__dfClearChartLoading();
    });

    /*{# The turning glyph. A host that has a loading icon of its own marks it #}*/
    /*{# `data-dbt-loading-icon` and the board turns the same one, so the chart #}*/
    /*{# and the chrome agree; with no host icon (dct serve) it is a plain ring. #}*/
    /*{# rotate() turns about the SVG origin unless the box and origin are the #}*/
    /*{# element's own -- without those the glyph orbits the board instead. #}*/
    /*{# The host icon's paths are lifted into a <g>, never a nested <svg>: a #}*/
    /*{# CSS transform on an inner <svg> element corrupts the board's paint. #}*/
    var LOADING_GLYPH_SIZE = 24;
    var LOADING_GLYPH_PAINT = ['fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin'];
    function loadingGlyph(centerX, centerY) {
        var svgNS = 'http://www.w3.org/2000/svg';
        var hostIcon = document.querySelector('svg[data-dbt-loading-icon]');
        var glyph;
        if (hostIcon) {
            glyph = document.createElementNS(svgNS, 'g');
            LOADING_GLYPH_PAINT.forEach(function(attr) {
                if (hostIcon.hasAttribute(attr)) glyph.setAttribute(attr, hostIcon.getAttribute(attr));
            });
            for (var i = 0; i < hostIcon.children.length; i++) {
                glyph.appendChild(hostIcon.children[i].cloneNode(true));
            }
        } else {
            glyph = document.createElementNS(svgNS, 'circle');
            glyph.setAttribute('cx', LOADING_GLYPH_SIZE / 2);
            glyph.setAttribute('cy', LOADING_GLYPH_SIZE / 2);
            glyph.setAttribute('r', LOADING_GLYPH_SIZE / 2 - 1);
            glyph.setAttribute('fill', 'none');
            glyph.setAttribute('stroke', 'currentColor');
            glyph.setAttribute('stroke-width', '2');
            glyph.setAttribute('stroke-dasharray', '20 60');
        }
        glyph.style.transformBox = 'fill-box';
        glyph.style.transformOrigin = 'center';
        glyph.style.animation = 'dbt-spin 0.8s linear infinite';
        /*{# Position on an outer group: the animation owns the glyph's own transform. #}*/
        var placed = document.createElementNS(svgNS, 'g');
        placed.setAttribute('transform', 'translate('
            + (centerX - LOADING_GLYPH_SIZE / 2) + ',' + (centerY - LOADING_GLYPH_SIZE / 2) + ')');
        placed.appendChild(glyph);
        return placed;
    }

    /*{# The drawn control publishes the committed value, and the render is the #}*/
    /*{# only thing that has written it — so between a commit and the re-render #}*/
    /*{# that answers it, the attribute still holds the OLD value. Any reader in #}*/
    /*{# that window (getAllVariableValues, below) sees the value the user just #}*/
    /*{# replaced. Write it back at the moment of commit so the published state #}*/
    /*{# and the committed state never disagree, and every reader stays a reader. #}*/
    function publishCommitted(name, value) {
        var el = document.querySelector(
            '[data-dbt-variable="' + (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]'
        );
        if (!el) return;   /*{# hidden variable: nothing drawn to update #}*/
        if (el.getAttribute('data-dbt-input') === 'checkbox') {
            el.setAttribute('data-dbt-checked', value ? 'true' : 'false');
        }
        /*{# Same emptiness test the URL branch uses, so a cleared control reads #}*/
        /*{# back as unset. `false` is not one: it is the value an unticked #}*/
        /*{# checkbox commits, and publishing "" for it hides that from a host. #}*/
        var empty = value === '';
        /*{# A list is published as JSON: that is the shape a host reads back #}*/
        /*{# (getAllVariableValues), and the URL is the only place it repeats. #}*/
        el.setAttribute('data-dbt-value',
            empty ? '' : Array.isArray(value) ? JSON.stringify(value) : String(value));
    }

    /*{# A framed board's host is its parent frame, unless the board's own #}*/
    /*{# document is the host — one whose parent speaks no dbt messages — #}*/
    /*{# which registers __dfCommitFramedVariables to take the commits. #}*/
    /*{# Using '*' is safe here because: #}*/
    /*{# 1. Iframe content is generated by us (not user input) #}*/
    /*{# 2. Parent checks message.type before processing #}*/
    /*{# 3. Blob URLs have null origin, so specific origin targeting doesn't work #}*/
    function commitToFrameHost(vars) {
        if (typeof window.__dfCommitFramedVariables === 'function') {
            window.__dfCommitFramedVariables(vars);
            return;
        }
        window.parent.postMessage({
            type: 'dbt-variable-change',
            variables: vars
        }, '*');
    }

    /*{# The view param of every chart that holds one, by param: the engine #}*/
    /*{# stamps it and its value on the chart. A view is no drawn control, so a #}*/
    /*{# host that replaces its whole variable state would otherwise lose it. #}*/
    function activeLegendViews(root) {
        var views = {};
        root.querySelectorAll('[data-dbt-legend-view]').forEach(function(chart) {
            views[chart.getAttribute('data-dbt-legend-view')] = JSON.parse(chart.getAttribute('data-dbt-legend-active'));
        });
        return views;
    }

    /*{# Commit one or more variables in a single URL rewrite, frame-host #}*/
    /*{# message, or navigation. `changes` maps name to value; a null value is #}*/
    /*{# "the default". #}*/
    function updateVariables(changes) {
        var names = Object.keys(changes);
        /*{# Mark dependent charts as loading. The state ends when the host #}*/
        /*{# replaces the board (swap, or the navigation below) or clears it on #}*/
        /*{# a failed render (__dfClearChartLoading) -- never on a timer. #}*/
        names.forEach(function(name) {
            markDependentChartsLoading(name);
            /*{# The default is unknown here, so there is nothing to publish; #}*/
            /*{# the next render redraws the control with it. #}*/
            if (changes[name] !== null) publishCommitted(name, changes[name]);
        });

        /*{# Update URL. Absent and empty are different states and the server #}*/
        /*{# keeps them apart: no param means "the default", `?name=` means "no #}*/
        /*{# value" — the All row, a cleared text field, an emptied list. So '' #}*/
        /*{# is *set*, not deleted: deleting it collapsed the two, and on any #}*/
        /*{# control with a default, All put the default straight back. Only #}*/
        /*{# null deletes. `false` is a value too — a checkbox's other one. #}*/
        var url = new URL(window.location);
        names.forEach(function(name) {
            var value = changes[name];
            url.searchParams.delete(name);
            if (Array.isArray(value)) {
                /*{# A list is a repeated key — `?region=west&region=east`, the way #}*/
                /*{# a form sends a <select multiple> — so a link is hand-writable #}*/
                /*{# and each member is its own param. An empty list is one #}*/
                /*{# explicit empty, the same spelling every other empty uses. #}*/
                if (!value.length) url.searchParams.append(name, '');
                value.forEach(function(member) { url.searchParams.append(name, String(member)); });
            } else if (value !== null) {
                url.searchParams.set(name, String(value));
            }
        });

        /*{# Notify the frame host (suite/playground) or reload (standalone) #}*/
        if (window.parent !== window) {
            /*{# The changed params take their pending value below, not the one #}*/
            /*{# the chart was drawn with. #}*/
            var views = activeLegendViews(document);
            names.forEach(function(name) { delete views[name]; });
            var vars = Object.assign(views, getAllVariableValues());
            /*{# publishCommitted has already written each value's attribute, #}*/
            /*{# so a drawn control reports the new value. A variable with no #}*/
            /*{# drawn control (hidden: true) has nothing to read, and the #}*/
            /*{# pending commit is its only source. A null overrides the drawn #}*/
            /*{# value, which still holds the one being replaced. #}*/
            names.forEach(function(name) {
                if (changes[name] === null) vars[name] = null;
                else setVariableIfMissing(vars, name, changes[name]);
            });
            commitToFrameHost(vars);
        } else if (typeof window.__dfHandleVariableUpdate === 'function') {
            /*{# Suite registers this hook to re-render in place (no page reload). #}*/
            window.__dfHandleVariableUpdate(url);
        } else {
            /*{# Plain dct serve: full page reload. Save scroll so #}*/
            /*{# restoreScrollIfSaved can put it back after the new page renders. #}*/
            try {
                sessionStorage.setItem('__dfScrollY_' + window.location.pathname, String(window.scrollY));
                sessionStorage.setItem(_MENU_OPEN_KEY + window.location.pathname, JSON.stringify(_openMenus()));
            } catch (e) { /*{# sessionStorage may be unavailable #}*/ }
            document.documentElement.classList.add('dbt-page-loading');
            _armNavigationCancelGuard();
            window.location.href = url.toString();
        }
    }

    function updateVariable(name, value) {
        var changes = {};
        changes[name] = value;
        updateVariables(changes);
    }

    /*{# Exposed for the inline handlers the control templates carry #}*/
    /*{# (onchange="updateVariable(...)"). #}*/
    window.updateVariable = updateVariable;

    var _popoverInstances = [];

    /*{# A host swaps boards in and out; a popover built for a control that is #}*/
    /*{# no longer in the document would otherwise sit on document.body forever, #}*/
    /*{# and its instance would keep answering the outside-click and Esc #}*/
    /*{# handlers. Both go when the control does. #}*/
    /*{# Menus open when a render lands. A host replaces the board's nodes, so #}*/
    /*{# the menu hanging off the old trigger is dropped with it — and a #}*/
    /*{# multiselect, which exists for picking several, shut on the first pick. #}*/
    /*{# The name is kept, with the view the menu was showing — its search, #}*/
    /*{# sort, and selected-only toggle — and mount() opens that menu again on #}*/
    /*{# the new trigger. #}*/
    var _reopenAfterMount = {};
    var _MENU_OPEN_KEY = '__dfMenuView_';

    function _keepOpenMenu(menus, inst) {
        menus[inst.trigger.getAttribute('data-dbt-variable')] = inst.view ? inst.view() : null;
    }

    function _openMenus() {
        var menus = {};
        _popoverInstances.forEach(function(inst) {
            if (inst.isOpen()) _keepOpenMenu(menus, inst);
        });
        return menus;
    }

    function _dropDetachedPopovers() {
        _popoverInstances = _popoverInstances.filter(function(inst) {
            if (inst.trigger.isConnected) return true;
            if (inst.isOpen()) _keepOpenMenu(_reopenAfterMount, inst);
            if (inst.popover && inst.popover.parentNode) inst.popover.remove();
            return false;
        });
    }

    function _reopenMenus() {
        try {
            var key = _MENU_OPEN_KEY + window.location.pathname;
            var saved = sessionStorage.getItem(key);
            if (saved !== null) {
                sessionStorage.removeItem(key);
                Object.assign(_reopenAfterMount, JSON.parse(saved));
            }
        } catch (e) { /*{# sessionStorage may be unavailable #}*/ }
        var menus = _reopenAfterMount;
        _reopenAfterMount = {};
        _popoverInstances.forEach(function(inst) {
            var name = inst.trigger.getAttribute('data-dbt-variable');
            /*{# A variable may be named `constructor`; `in` would find it on every object. #}*/
            if (Object.prototype.hasOwnProperty.call(menus, name) && !inst.isOpen()) {
                inst.open(menus[name]);
            }
        });
    }

    /*{# Same story for a lifted native input. Being page-level is the whole #}*/
    /*{# point — that is what keeps it out of the board's scaled coordinate #}*/
    /*{# system — but it also means removing the board does not remove it, so #}*/
    /*{# it would float over whatever the host swaps in next. Dismissing rather #}*/
    /*{# than removing matters: its own teardown must not read a commit out of #}*/
    /*{# a board swap and navigate the page on the way down. #}*/
    function _dropOrphanedOverlays() {
        document.querySelectorAll('.dbt-variable-overlay').forEach(function(overlay) {
            if (overlay.__dbtOwner && !overlay.__dbtOwner.isConnected) {
                overlay.__dbtDismiss();
            }
        });
    }

    /*{# The click that opens a popover no longer stops here — a host reads its #}*/
    /*{# own meaning off it, which is how Cloud selects the variable. But a host #}*/
    /*{# may also *answer* it synchronously with a click of its own: Cloud opens #}*/
    /*{# the Design panel by clicking the sidebar tab, and that click bubbles #}*/
    /*{# back to the document handler below with a target outside every popover. #}*/
    /*{# Dismissing on it would close the dropdown the user just opened, on the #}*/
    /*{# first click only. So a popover is not dismissible in the turn that #}*/
    /*{# opened it: a real click away is a second gesture, and a second gesture #}*/
    /*{# is always a later task. #}*/
    var _openingTurn = false;

    function _registerPopover(inst) {
        _popoverInstances.push(inst);
        inst.trigger.addEventListener('click', function() {
            _openingTurn = true;
            setTimeout(function() { _openingTurn = false; }, 0);
            _popoverInstances.forEach(function(other) {
                if (other !== inst) other.close();
            });
        });
    }

    /*{# An open popover is dismissible from outside itself, or it is a trap: it #}*/
    /*{# is page-level and fixed, so it sits over the board until its own trigger #}*/
    /*{# is found again — and a multiselect deliberately stays open through option #}*/
    /*{# clicks, so it has no self-closing path at all. These also complete the #}*/
    /*{# ARIA combobox contract the drawn controls advertise (role + aria-expanded). #}*/
    function _closeOpenPopovers(except) {
        _popoverInstances.forEach(function(inst) {
            if (inst !== except && inst.isOpen()) inst.close();
        });
    }

    /*{# Focus is granted here, once, for every drawn control — never inside a #}*/
    /*{# binding. Clicking an SVG <g> does not focus it the way clicking an input #}*/
    /*{# does, and granting it per binding meant any control that skipped the #}*/
    /*{# helper (or a future one) silently kept the ring on whatever was operated #}*/
    /*{# last, pointing at one control while another's menu stood open. On #}*/
    /*{# pointerdown, not click: that is when a browser grants focus, so a lifted #}*/
    /*{# text overlay blurs with this control as its relatedTarget and commits #}*/
    /*{# itself away instead of sitting behind the new menu. The label run is #}*/
    /*{# the authoring handle, not the value surface — a host answers that press #}*/
    /*{# by revealing YAML, so the control must not take the caret from it. #}*/
    if (!_documentWired) document.addEventListener('pointerdown', function(event) {
        if (!event.target.closest) return;
        var control = event.target.closest('[data-dbt-variable]');
        if (!control || !control.focus) return;
        if (control.getAttribute('data-dbt-enabled') === 'false') return;
        var run = event.target.closest('[data-authored-kind="label"]');
        if (run && control.contains(run)) return;
        control.focus();
    });

    if (!_documentWired) {
        document.addEventListener('click', function(event) {
            if (_openingTurn) return;
            var inside = _popoverInstances.filter(function(inst) {
                return inst.popover.contains(event.target)
                    || inst.trigger.contains(event.target);
            })[0];
            /*{# The trigger's own handler toggles it; closing it here too would #}*/
            /*{# reopen-and-close on a single click. #}*/
            _closeOpenPopovers(inside);
        });
        document.addEventListener('keydown', function(event) {
            if (event.key !== 'Escape') return;
            var open = _popoverInstances.filter(function(inst) { return inst.isOpen(); });
            if (!open.length) return;
            /*{# Spent on the popover: a host must not also act on it (the link #}*/
            /*{# modal would close). #}*/
            event.preventDefault();
            /*{# Focus goes back to the control that opened it — Esc in a combobox #}*/
            /*{# returns you where you were, it does not drop you at the page top. #}*/
            open.forEach(function(inst) { inst.close(); });
            if (open[0].trigger.focus) open[0].trigger.focus();
        });
    }

    /*{# A paginator button on a live board names the variable it drives; the #}*/
    /*{# server writes no onclick into the SVG. A static export's button names #}*/
    /*{# only its target page, so this leaves it alone. #}*/
    if (!_documentWired) document.addEventListener('click', function(event) {
        if (!event.target.closest) return;
        var button = event.target.closest('[data-dbt-page-var]');
        if (!button) return;
        updateVariable(button.getAttribute('data-dbt-page-var'), button.getAttribute('data-dbt-page-target'));
    });

    /*{# A modal link opens the target board chrome-less (?_embed) in the #}*/
    /*{# shared link modal (link_modal.js), created on first use. #}*/
    var _inLinkModal = window.parent !== window
        && window.name === 'dbt-link-modal'; /*{# link_modal.js FRAME_NAME #}*/
    var _linkModal = null;

    function _openLinkModal(path, from, how) {
        if (!_linkModal) {
            _linkModal = window.dbtLinkModal.create(function(href) {
                var url = new URL(window.location.origin + href);
                url.searchParams.set('_embed', '');
                return url.href;
            }, function(href) {
                return window.location.origin + href;
            });
        }
        _linkModal.open(path, from, how);
    }

    /*{# The root-relative path a link names on this origin, or null: an #}*/
    /*{# in-page anchor, another origin, or a `//` path a URL parser reads as #}*/
    /*{# another host. A blob URL has no hierarchy to resolve against. #}*/
    function _boardPath(href) {
        if (/^\/(?!\/)/.test(href)) return href;
        if (href.charAt(0) === '#') return null;
        var base = window.location.protocol === 'blob:'
            ? window.location.origin + '/' : window.location.href;
        var target;
        try { target = new URL(href, base); } catch (e) { return null; }
        if (!/^https?:$/.test(target.protocol) || target.origin !== window.location.origin
            || target.pathname.indexOf('//') === 0) {
            return null;
        }
        return target.pathname + target.search + target.hash;
    }

    /*{# Escape inside the modal's board never reaches the host's dialog. #}*/
    if (!_documentWired && _inLinkModal) document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && !event.defaultPrevented) {
            window.parent.postMessage({ type: 'dbt-modal-escape' }, '*');
        }
    });

    /*{# ── Intercept clicks on SVG <a href="?..."> links ──────────────────────── #}*/
    /*{# Blob URL iframes can't navigate to query-string URLs, so we parse the #}*/
    /*{# params ourselves and send a single postMessage with the merged variable #}*/
    /*{# snapshot instead of navigating. #}*/
    if (!_documentWired) document.addEventListener('click', function(event) {
        var link = event.target.closest('a[href]');
        if (!link) return;
        if (!link.ownerSVGElement && link.namespaceURI !== 'http://www.w3.org/2000/svg') return;
        var href = link.getAttribute('href');
        if (!href) return;
        if (href.charAt(0) !== '?') {
            /*{# The mark has no clearing path outside a board swap, a failed- #}*/
            /*{# render host clear, or the bfcache restore below -- so it must #}*/
            /*{# not be applied to a click that will not replace this document. #}*/
            /*{# `.target` on an SVGAElement is an SVGAnimatedString, not a #}*/
            /*{# plain string -- the attribute is the only portable read. #}*/
            var linkTarget = link.getAttribute('target');
            var openIn = link.getAttribute('data-dbt-link-open');
            /*{# A framed board's own tab would resolve the board path against #}*/
            /*{# the frame (a blob URL in the playground): its host opens it. #}*/
            var framedTab = openIn === 'new_tab' && window.parent !== window;
            if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey ||
                (linkTarget && linkTarget !== '_self' && !framedTab) ||
                link.hasAttribute('download')
            ) {
                return;
            }
            /*{# A modal link keeps its real href, so a modified click above still #}*/
            /*{# opens a tab and a host without this script still navigates. #}*/
            var isModalLink = openIn === 'modal' || openIn === 'preview';
            if (window.parent !== window) {
                /*{# A framed board never loads a board itself (a blob frame can't #}*/
                /*{# resolve a relative URL; the host owns the modal). Inside the #}*/
                /*{# modal, another site opens a tab: most refuse to be framed. #}*/
                var framedPath = _boardPath(href);
                if (framedPath !== null) {
                    event.preventDefault();
                    /*{# The mark's box, in this frame's viewport: the host grows #}*/
                    /*{# the modal out of it. #}*/
                    var mark = link.getBoundingClientRect();
                    window.parent.postMessage({
                        type: framedTab ? 'dbt-board-open-tab'
                            : isModalLink ? 'dbt-board-open-modal' : 'dbt-board-navigate',
                        href: framedPath,
                        open: openIn,
                        rect: { left: mark.left, top: mark.top, width: mark.width, height: mark.height }
                    }, '*');
                    return;
                }
                /*{# Another site: the browser opens the tab itself. #}*/
                if (framedTab) return;
                if (_inLinkModal && /^https?:\/\//i.test(href)) {
                    event.preventDefault();
                    window.open(href, '_blank', 'noopener,noreferrer');
                    return;
                }
            } else if (isModalLink) {
                /*{# Another origin can't be framed; it gets a tab instead. #}*/
                var modalPath = _boardPath(href);
                event.preventDefault();
                if (modalPath !== null) {
                    _openLinkModal(modalPath, link.getBoundingClientRect(), openIn);
                } else {
                    window.open(new URL(href, window.location.href).href, '_blank', 'noopener,noreferrer');
                }
                return;
            }
            /*{# `board_links.py`'s passthrough prefixes (mailto:, #, an editor #}*/
            /*{# deeplink, ...) reach the browser unrewritten, and none of them #}*/
            /*{# unload the document -- only an http(s) navigation to a #}*/
            /*{# different document does. #}*/
            var willReplaceDocument;
            try {
                var target = new URL(href, window.location.href);
                willReplaceDocument = (target.protocol === 'http:' || target.protocol === 'https:')
                    && target.href.split('#')[0] !== window.location.href.split('#')[0];
            } catch (e) {
                willReplaceDocument = false;
            }
            if (!willReplaceDocument) return;
            var group = link.closest('[data-chart-id]');
            if (group) {
                window.__dfMarkChartLoading(group);
                _armNavigationCancelGuard();
            }
            return;
        }
        var inIframe = window.parent !== window;
        var hasHook = typeof window.__dfHandleVariableUpdate === 'function';
        var params = new URLSearchParams(href.slice(1));
        if (!params.toString()) {
            if (inIframe || hasHook) event.preventDefault();
            return;
        }

        if (!inIframe && !hasHook) {
            /*{# Standalone dct serve: let the browser navigate, but save scroll #}*/
            /*{# first so restoreScrollIfSaved() can recover position after reload. #}*/
            try {
                sessionStorage.setItem('__dfScrollY_' + window.location.pathname, String(window.scrollY));
            } catch (e) { /*{# sessionStorage may be unavailable #}*/ }
            return; /*{# browser follows the link naturally #}*/
        }

        event.preventDefault();
        var vars = getAllVariableValues();
        var tabUrl = inIframe ? null : new URL(window.location);
        params.forEach(function(value, name) {
            markDependentChartsLoading(name);
            vars[name] = value;
            if (tabUrl) { tabUrl.searchParams.set(name, value); }
        });
        if (inIframe) {
            commitToFrameHost(vars);
        } else {
            window.__dfHandleVariableUpdate(tabUrl);
        }
    });

    /*{# Collect all variable values from form controls #}*/
    /*{# The board's committed variable state, read off the drawn controls. #}*/
    /*{# Each publishes `data-dbt-value` in the form the query path uses, so #}*/
    /*{# there is nothing to infer from a widget's DOM shape any more — which is #}*/
    /*{# what the per-class ladder this replaced was doing. #}*/
    function getAllVariableValues() {
        var allVars = {};
        document.querySelectorAll('[data-dbt-variable]').forEach(function(el) {
            var name = el.getAttribute('data-dbt-variable');
            var raw = el.getAttribute('data-dbt-value') || '';
            var input = el.getAttribute('data-dbt-input');
            if (input === 'checkbox') {
                allVars[name] = el.getAttribute('data-dbt-checked') === 'true';
            } else if (input === 'multiselect' || input === 'daterange') {
                /*{# List-valued: published as JSON so a member may contain any #}*/
                /*{# delimiter. A malformed value is empty, never a raw string #}*/
                /*{# the query path would filter on literally. A daterange preset #}*/
                /*{# id is the one string value. #}*/
                try {
                    var parsed = JSON.parse(raw);
                    allVars[name] = Array.isArray(parsed) ? parsed : [];
                } catch (e) {
                    allVars[name] = input === 'daterange' && raw ? raw : [];
                }
            } else if (input === 'number' || input === 'slider' || input === 'range') {
                allVars[name] = raw === '' ? null : parseFloat(raw);
            } else {
                allVars[name] = raw;
            }
        });
        return allVars;
    }

    /*{# Restore scroll position saved before a standalone page reload (dct serve). #}*/
    function restoreScrollIfSaved() {
        try {
            var key = '__dfScrollY_' + window.location.pathname;
            var saved = sessionStorage.getItem(key);
            if (saved !== null) {
                sessionStorage.removeItem(key);
                window.scrollTo(0, parseInt(saved, 10));
            }
        } catch (e) { /*{# sessionStorage may be unavailable #}*/ }
    }

    /*{# Hovering a control outlines the charts it filters. Re-anchored to the #}*/
    /*{# drawn control, which names its variable directly — the wrapper-then- #}*/
    /*{# inner-input lookup this replaced was reading the deleted layer's markup. #}*/
    function setupVariableHoverHighlighting(root) {
        var controls = root.querySelectorAll('[data-dbt-variable]');
        for (var i = 0; i < controls.length; i++) {
            var control = controls[i];
            var varName = control.getAttribute('data-dbt-variable');
            if (!varName) continue;

            control.addEventListener('mouseenter', function(vName) {
                return function() {
                    var charts = document.querySelectorAll('[data-var-' + vName + ']');
                    for (var j = 0; j < charts.length; j++) {
                        var chart = charts[j];

                        /*{# Use transparent overlay for highlighting (cleaner than offset rects) #}*/
                        var existingOverlay = chart.querySelector('.dbt-chart-highlight-overlay');
                        if (!existingOverlay) {
                            try {
                                /*{# Use allocated dimensions from data attributes (set by renderer) #}*/
                                var chartWidth = parseFloat(chart.getAttribute('data-chart-width')) || 0;
                                var chartHeight = parseFloat(chart.getAttribute('data-chart-height')) || 0;

                                /*{# Fallback to getBBox if data attributes not available #}*/
                                if (chartWidth === 0 || chartHeight === 0) {
                                    var bbox = chart.getBBox ? chart.getBBox() : null;
                                    if (bbox) {
                                        chartWidth = bbox.width;
                                        chartHeight = bbox.height;
                                    }
                                }

                                if (chartWidth > 0 && chartHeight > 0) {
                                    /*{# Create overlay rect that covers entire chart area #}*/
                                    var overlay = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                                    overlay.setAttribute('class', 'dbt-chart-highlight-overlay');
                                    overlay.setAttribute('x', '0');
                                    overlay.setAttribute('y', '0');
                                    overlay.setAttribute('width', chartWidth);
                                    overlay.setAttribute('height', chartHeight);
                                    overlay.setAttribute('rx', '4'); /*{# Slight rounding #}*/
                                    /*{# Insert at end so it's on top (but pointer-events: none allows interaction) #}*/
                                    chart.appendChild(overlay);
                                }
                            } catch (e) {
                                /*{# Dimension calculation may fail, ignore silently #}*/
                            }
                        }
                    }
                };
            }(varName));

            control.addEventListener('mouseleave', function(vName) {
                return function() {
                    var charts = document.querySelectorAll('[data-var-' + vName + ']');
                    for (var j = 0; j < charts.length; j++) {
                        var chart = charts[j];

                        /*{# Remove highlight overlay #}*/
                        var highlightOverlay = chart.querySelector('.dbt-chart-highlight-overlay');
                        if (highlightOverlay) {
                            highlightOverlay.remove();
                        }
                    }
                };
            }(varName));
        }
    }

    /*{# ── Popover placement ──────────────────────────────────────────────────── #}*/

    /*{# A popover is page-level and viewport-positioned while open: it must not #}*/
    /*{# sit inside the board, because a fixed element under a scaled ancestor #}*/
    /*{# inherits that scale and the menu would shrink with the board. #}*/
    function _floatPopover(popover) {
        popover.style.position = 'fixed';
    }

    /*{# Drop the inline position so the stylesheet governs the closed popover. #}*/
    function _unfloatPopover(popover) {
        popover.style.position = '';
        popover.style.top = '';
        popover.style.left = '';
    }

    /*{# A drawn control is `<g>[label][field]`, and the <g> is the tab stop and #}*/
    /*{# the combobox — so its client rect spans the label too. The menu belongs #}*/
    /*{# under the field the user clicked, so anchor off the field box the render #}*/
    /*{# already marks. Controls without one (nothing today) fall back to the #}*/
    /*{# group rather than losing their placement. #}*/
    function _anchorBoxOf(trigger) {
        return trigger.querySelector('[data-dbt-field]') || trigger;
    }

    /*{# Position from the anchor's viewport coords, flipping to a right-edge #}*/
    /*{# anchor when a left anchor would overflow. getBoundingClientRect() on an #}*/
    /*{# SVG element returns its on-screen box, so this works against a drawn #}*/
    /*{# control unchanged. #}*/
    function _positionPopoverFixed(trigger, popover) {
        var rect = _anchorBoxOf(trigger).getBoundingClientRect();
        popover.style.top = (rect.bottom + 6) + 'px';
        var popoverWidth = popover.getBoundingClientRect().width;
        var margin = 8;
        if (rect.left + popoverWidth > window.innerWidth - margin) {
            popover.style.left = Math.max(margin, rect.right - popoverWidth) + 'px';
        } else {
            popover.style.left = rect.left + 'px';
        }
    }

    /*{# A fixed element does not follow its anchor, so every open one — a #}*/
    /*{# popover, or the native input lifted over a text field — is re-placed #}*/
    /*{# on scroll and resize. One mechanism for everything that floats. #}*/
    function _repositionOpenPopovers() {
        _popoverInstances.forEach(function(inst) {
            if (inst.isOpen()) _positionPopoverFixed(inst.trigger, inst.popover);
        });
        document.querySelectorAll('.dbt-variable-overlay').forEach(function(overlay) {
            if (overlay.__dbtPlace) overlay.__dbtPlace();
        });
    }

    /*{# ── Binding ────────────────────────────────────────────────────────────── #}*/

    /*{# The server draws every control into the board SVG and publishes each #}*/
    /*{# one's identity, resolved widget, and box. Nothing here lays anything #}*/
    /*{# out: a board is a fixed-viewBox SVG the host stretches to fit, so any #}*/
    /*{# element positioned over it is measured in page pixels while the space #}*/
    /*{# reserved for it was measured in board units — the two move in opposite #}*/
    /*{# directions as the board scales. Binding behavior to what is already #}*/
    /*{# drawn is what makes that whole class of bug unreachable. #}*/

    /*{# ARIA role per resolved widget. A drawn control is a <g>, which carries #}*/
    /*{# no semantics of its own — a screen reader gets whatever we declare and #}*/
    /*{# nothing more, so an unmapped widget is a control nobody can operate. #}*/
    var _ROLE_BY_INPUT = {
        select: 'combobox',
        multiselect: 'combobox',
        radio: 'combobox',
        checkbox: 'checkbox',
        date: 'button',
        datepicker: 'button',
        daterange: 'button',
        slider: 'slider',
        range: 'slider',
        text: 'textbox',
        input: 'textbox',
        textarea: 'textbox',
        number: 'spinbutton'
    };

    function _controlLabel(el) {
        var text = el.querySelector('text');
        return text ? text.textContent.replace(/:$/, '') : '';
    }

    /*{# A control is the one drawn thing whose primary gesture is *using* it, #}*/
    /*{# so a host has nowhere to mean "select this variable" unless some part #}*/
    /*{# of it is not a value surface. The renderer tags the label run — the #}*/
    /*{# one part of a control that is not a value — and every pointer gesture #}*/
    /*{# declines it, so selecting a variable or renaming its label never rides #}*/
    /*{# on top of a filter change. An untagged label means no handle at all #}*/
    /*{# (imported board), and then the whole control is value surface again. #}*/
    /*{# Keyboard is unsplit: the control is one tab stop, Enter operates it. #}*/
    function _onValueGesture(el, handler) {
        el.addEventListener('click', function(e) {
            var run = e.target.closest('[data-authored-kind="label"]');
            if (run && el.contains(run)) return;
            handler(e);
        });
    }

    /*{# Checkbox commits in place: no overlay, no popover, nothing to position. #}*/
    /*{# Build a select popover at page level from the options the render #}*/
    /*{# published. Page-level is the whole point: a popover inside the board #}*/
    /*{# would inherit the board's scale, and a fixed element under a scaled #}*/
    /*{# ancestor inherits that scale too — menus would shrink with the board. #}*/
    /*{# At rest we are in board coordinates; on interaction, viewport ones. #}*/
    /*{# The committed selection as a list, however it was published: a JSON #}*/
    /*{# list, a bare scalar from a URL written before the control was a list, #}*/
    /*{# or '' for nothing. This is the one source the rows are lit from. #}*/
    function _committedList(el) {
        var raw = el.getAttribute('data-dbt-value') || '';
        if (raw === '') return [];
        try {
            var parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [raw];
        } catch (e) { return [raw]; }
    }

    /*{# Every row lit from `chosen` and nothing else — the unset row exactly #}*/
    /*{# when nothing is chosen, a member exactly when it is. Rows never toggle #}*/
    /*{# themselves: state that lives in the DOM is state that drifts, and it #}*/
    /*{# did — "All" and a member lit at once. #}*/
    function _reflectSelection(popover, chosen) {
        popover.querySelectorAll('.dbt-select-option').forEach(function(row) {
            var lit = row.hasAttribute('data-unset')
                ? !chosen.length
                : chosen.indexOf(row.getAttribute('data-value')) !== -1;
            row.setAttribute('aria-selected', lit ? 'true' : 'false');
        });
    }

    /*{# The menu's height shows nine rows, so ten is the first list that scrolls. #}*/
    var _SEARCH_MIN_OPTIONS = 10;

    /*{# A number inside a value sorts by its value, so "Team 9" precedes #}*/
    /*{# "Team 10"; case and accents do not split the alphabet in two. #}*/
    var _OPTION_COLLATOR = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});

    function _buildSelectPopover(el, varName) {
        var raw = el.getAttribute('data-dbt-options');
        var canUnset = el.getAttribute('data-dbt-can-unset') === 'true';
        var values = [];
        if (raw) {
            try { values = JSON.parse(raw); } catch (e) { values = []; }
        }
        /*{# An empty option list is still openable when the control can be #}*/
        /*{# returned to unset — that row is the whole menu. #}*/
        if (!values.length && !canUnset) return null;

        var popover = document.createElement('div');
        /*{# Both classes: .dbt-popover is the card — display:none included — and #}*/
        /*{# .dbt-select-popover only modifies it. Setting the modifier alone #}*/
        /*{# leaves every select's options rendered, unstyled, on the page. #}*/
        popover.className = 'dbt-popover dbt-select-popover';
        /*{# The card holds the search; a listbox may hold only options. #}*/
        var list = document.createElement('div');
        list.className = 'dbt-select-list';
        list.setAttribute('role', 'listbox');
        /*{# On the listbox, where ARIA puts it — and where the stylesheet reads #}*/
        /*{# it to draw a checkbox on every member row: the one cue that tells a #}*/
        /*{# multi from a single before anything is clicked. #}*/
        if (el.getAttribute('data-dbt-input') === 'multiselect') {
            list.setAttribute('aria-multiselectable', 'true');
        }

        /*{# A no-default, non-required control offers its way back to unset. #}*/
        /*{# Without this row the user can filter and never unfilter, since a #}*/
        /*{# commit's delete-the-param branch is unreachable from the UI. #}*/
        if (canUnset) {
            var clear = document.createElement('div');
            clear.className = 'dbt-select-option dbt-select-unset';
            clear.setAttribute('role', 'option');
            /*{# Marked by attribute, not by the class: the class is the row's #}*/
            /*{# appearance and this is what it *is* — the way back to unset. #}*/
            clear.setAttribute('data-unset', 'true');
            clear.setAttribute('data-value', '');
            /*{# No `|| 'All'` fallback: the server emits this attribute on #}*/
            /*{# every can_unset control, and can_unset is the only branch that #}*/
            /*{# builds this row. A default here would silently diverge from #}*/
            /*{# read_only_unset_label the day that label changes. #}*/
            clear.textContent = el.getAttribute('data-dbt-unset-label');
            list.appendChild(clear);
        }

        /*{# [count, text] per value, aligned with the values, when the options #}*/
        /*{# carry counts: the number is what a count sort compares, the text is #}*/
        /*{# what the row draws, already in the board's format. #}*/
        var rawCounts = el.getAttribute('data-dbt-option-counts');
        var counts = rawCounts ? JSON.parse(rawCounts) : [];
        values.forEach(function(value, i) {
            var opt = document.createElement('div');
            opt.className = 'dbt-select-option';
            opt.setAttribute('role', 'option');
            opt.setAttribute('data-value', value);
            opt.id = 'dbt-opt-' + varName + '-' + i;
            if (counts.length) {
                var label = document.createElement('span');
                label.className = 'dbt-select-label';
                label.textContent = value;
                var count = document.createElement('span');
                count.className = 'dbt-select-count';
                count.textContent = counts[i][1];
                opt.classList.add('dbt-select-counted');
                opt.append(label, count);
                opt.setAttribute('data-count', counts[i][0]);
            } else {
                opt.textContent = value;
            }
            list.appendChild(opt);
        });
        /*{# A list long enough to scroll is long enough to lose a value in, so #}*/
        /*{# it gets the search, a sort beside it, and — on a multiselect, whose #}*/
        /*{# ticks scatter through the list — a footer toggle to only them. #}*/
        var only = null;
        if (values.length >= _SEARCH_MIN_OPTIONS) {
            var words = _words(el);
            var bar = document.createElement('div');
            bar.className = 'dbt-select-bar';
            var search = document.createElement('input');
            search.type = 'search';
            search.className = 'dbt-select-search';
            search.placeholder = words.search;
            search.setAttribute('aria-label', words.search_values);
            var sort = document.createElement('button');
            sort.type = 'button';
            sort.className = 'dbt-select-sort';
            sort.setAttribute('aria-label', words.sort);
            sort.setAttribute('aria-haspopup', 'menu');
            sort.setAttribute('aria-expanded', 'false');
            bar.appendChild(search);
            bar.appendChild(sort);
            popover.appendChild(bar);
            var sortMenu = document.createElement('div');
            sortMenu.className = 'dbt-select-sort-menu';
            sortMenu.setAttribute('role', 'menu');
            sortMenu.hidden = true;
            var sorts = [['asc', 'sort_text_ascending'], ['desc', 'sort_text_descending'], ['', 'clear_sort']];
            if (counts.length) {
                sorts.unshift(['count-desc', 'sort_count_descending'], ['count-asc', 'sort_count_ascending']);
            }
            sorts.forEach(function(item) {
                    var pick = document.createElement('button');
                    pick.type = 'button';
                    pick.setAttribute('role', item[0] ? 'menuitemradio' : 'menuitem');
                    pick.setAttribute('data-sort', item[0]);
                    pick.textContent = words[item[1]];
                    sortMenu.appendChild(pick);
                });
            popover.appendChild(sortMenu);
            if (el.getAttribute('data-dbt-input') === 'multiselect') {
                only = document.createElement('button');
                only.type = 'button';
                only.className = 'dbt-select-only';
            }
        }
        popover.appendChild(list);
        if (only) popover.appendChild(only);
        _reflectSelection(popover, _committedList(el));
        document.body.appendChild(popover);
        return popover;
    }

    /*{# Select / multiselect: open the page-level popover anchored off the #}*/
    /*{# drawn control's client rect. getBoundingClientRect() on an SVG element #}*/
    /*{# returns its on-screen box, so the existing positioning machinery works #}*/
    /*{# against SVG unchanged. #}*/
    function _bindSelect(el, name) {
        var popover = _buildSelectPopover(el, name);
        if (!popover) {
            /*{# Nothing to open — an option query that returned empty, and no #}*/
            /*{# unset row either. Withdraw the affordances rather than leave a #}*/
            /*{# tab stop announced as a combobox that does nothing. #}*/
            el.removeAttribute('tabindex');
            el.removeAttribute('role');
            return;
        }

        var search = popover.querySelector('.dbt-select-search');
        var list = popover.querySelector('.dbt-select-list');
        var sortButton = popover.querySelector('.dbt-select-sort');
        var sortMenu = popover.querySelector('.dbt-select-sort-menu');
        var only = popover.querySelector('.dbt-select-only');
        var members = Array.prototype.slice.call(
            popover.querySelectorAll('.dbt-select-option:not([data-unset])'));
        var words = _words(el);
        var sorted = '';
        var selectedOnly = false;

        /*{# The unset row is not a value, so neither the search nor the #}*/
        /*{# selected-only view ever hides the way out. Unticking the last #}*/
        /*{# member leaves that view nothing to show, so it ends with it. #}*/
        function narrow() {
            var needle = search.value.toLowerCase();
            var ticked = members.filter(function(row) {
                return row.getAttribute('aria-selected') === 'true';
            }).length;
            if (!ticked) selectedOnly = false;
            members.forEach(function(row) {
                row.hidden = row.getAttribute('data-value').toLowerCase().indexOf(needle) === -1
                    || (selectedOnly && row.getAttribute('aria-selected') !== 'true');
            });
            if (only) {
                only.hidden = !ticked;
                only.textContent = selectedOnly
                    ? words.show_all : _fill(words.show_only_selected, {n: ticked});
            }
        }

        /*{# Reorders the member rows in place; '' is the order they came in. #}*/
        /*{# The sort is stable, so rows a count sort ties keep that order. #}*/
        function order(how) {
            sorted = how;
            var rows = members.slice();
            if (how) {
                var byCount = how === 'count-asc' || how === 'count-desc';
                rows.sort(function(a, b) {
                    var by = byCount
                        ? a.getAttribute('data-count') - b.getAttribute('data-count')
                        : _OPTION_COLLATOR.compare(a.getAttribute('data-value'), b.getAttribute('data-value'));
                    return how === 'desc' || how === 'count-desc' ? -by : by;
                });
            }
            rows.forEach(function(row) { list.appendChild(row); });
            sortButton.classList.toggle('dbt-select-sorted', !!how);
            sortMenu.querySelectorAll('[role="menuitemradio"]').forEach(function(pick) {
                pick.setAttribute('aria-checked', pick.getAttribute('data-sort') === how ? 'true' : 'false');
            });
        }

        function foldSortMenu(folded) {
            sortMenu.hidden = folded;
            sortButton.setAttribute('aria-expanded', folded ? 'false' : 'true');
        }

        function view() {
            return {query: search ? search.value : '', sort: sorted, only: selectedOnly};
        }

        function isOpen() { return popover.classList.contains('dbt-popover-open'); }
        function open(saved) {
            _floatPopover(popover);
            popover.classList.add('dbt-popover-open');
            if (search) {
                /*{# Held at no less than the full list's width: rows do not wrap, #}*/
                /*{# so the card would otherwise shrink and shift under the caret #}*/
                /*{# on each key. A floor, not a width: a menu reopened as the page #}*/
                /*{# loads is measured before the web font arrives, and has to grow #}*/
                /*{# when it does. #}*/
                narrow();
                popover.style.minWidth = Math.ceil(popover.getBoundingClientRect().width) + 'px';
                if (saved) {
                    search.value = saved.query;
                    selectedOnly = saved.only;
                    order(saved.sort);
                    narrow();
                }
            }
            _positionPopoverFixed(el, popover);
            el.setAttribute('aria-expanded', 'true');
            if (search) search.focus();
        }
        function close() {
            popover.classList.remove('dbt-popover-open');
            _unfloatPopover(popover);
            el.setAttribute('aria-expanded', 'false');
            if (search) {
                search.value = '';
                selectedOnly = false;
                order('');
                foldSortMenu(true);
                narrow();
                popover.style.minWidth = '';
            }
        }
        el.setAttribute('aria-expanded', 'false');
        /*{# The click keeps bubbling: a host draws its own meaning on a board #}*/
        /*{# click — Cloud selects the authored element under it — and a control #}*/
        /*{# that swallows the event is the one thing on the board it cannot #}*/
        /*{# see. The outside-click handler below already spares the trigger it #}*/
        /*{# came from, so nothing here needs stopPropagation to stay open. #}*/
        _onValueGesture(el, function() {
            if (isOpen()) close(); else open();
        });
        el.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });
        if (search) {
            search.addEventListener('input', narrow);
            search.addEventListener('keydown', function(e) {
                if (e.key !== 'Enter' || !search.value) return;
                e.preventDefault();
                var first = popover.querySelector('.dbt-select-option:not([hidden]):not([data-unset])');
                if (first) first.click();
            });
            sortButton.addEventListener('click', function() { foldSortMenu(!sortMenu.hidden); });
            sortMenu.addEventListener('click', function(e) {
                var pick = e.target.closest('[data-sort]');
                if (!pick) return;
                order(pick.getAttribute('data-sort'));
                foldSortMenu(true);
                search.focus();
            });
        }
        if (only) {
            only.addEventListener('click', function() {
                selectedOnly = !selectedOnly;
                narrow();
                search.focus();
            });
        }
        /*{# A multiselect commits a list, so it toggles members and stays open; #}*/
        /*{# a select commits one value and closes. Binding both as "pick one" #}*/
        /*{# silently narrowed every multiselect to its first choice. #}*/
        var multi = el.getAttribute('data-dbt-input') === 'multiselect';
        popover.addEventListener('click', function(e) {
            if (sortMenu && !e.target.closest('.dbt-select-sort, .dbt-select-sort-menu')) foldSortMenu(true);
            var opt = e.target.closest('.dbt-select-option');
            if (!opt) return;
            if (!multi) {
                close();
                _drawCommitted(el, opt.hasAttribute('data-unset')
                    ? el.getAttribute('data-dbt-unset-label') : opt.getAttribute('data-value'));
                updateVariable(name, opt.getAttribute('data-value'));
                return;
            }
            var value = opt.getAttribute('data-value');
            var committed = _committedList(el);
            var chosen = opt.hasAttribute('data-unset') ? []
                : committed.indexOf(value) === -1 ? committed.concat([value])
                : committed.filter(function(v) { return v !== value; });
            /*{# A required control with no default has no legal empty state: #}*/
            /*{# committing one renders the next page with no board, and so no #}*/
            /*{# control to get back from. Refuse — nothing has changed yet. #}*/
            if (!chosen.length && el.getAttribute('data-dbt-can-unset') !== 'true') return;
            _reflectSelection(popover, chosen);
            if (search) narrow();
            /*{# The unset row is the way out, so it closes; a member toggles and #}*/
            /*{# stays open for the next pick. Empty commits '' either way: one #}*/
            /*{# spelling of "nothing", which the server reads as the empty list. #}*/
            if (opt.hasAttribute('data-unset')) close();
            else if (search) search.focus();
            /*{# The render's own summary, ahead of it: the count, then the first tick. #}*/
            /*{# The render also cuts it to the field; this draws it whole meanwhile. #}*/
            _drawCommitted(el, chosen.length > 1
                ? _fill(words.selection_summary, {first: chosen[0], n: chosen.length})
                : chosen.length ? chosen[0] : el.getAttribute('data-dbt-unset-label'));
            updateVariable(name, chosen.length ? chosen : '');
        });
        _registerPopover({trigger: el, popover: popover, isOpen: isOpen, open: open, close: close, view: view});
    }

    /*{# The value the chrome drew for a field: the text placed at or past the #}*/
    /*{# field's own x, which is what place() sizes the lifted input from too. #}*/
    function _drawnValueText(el, field) {
        var drawnX = field && parseFloat(field.getAttribute('x'));
        if (!(drawnX >= 0)) return null;
        return Array.prototype.filter.call(el.querySelectorAll('text'), function(t) {
            return parseFloat(t.getAttribute('x')) >= drawnX;
        })[0] || null;
    }

    /*{# Draw what was committed now: the render that carries it is a round #}*/
    /*{# trip away, and until it lands the drawing would still read the value #}*/
    /*{# the user just replaced — a menu closed on a pick and still showing #}*/
    /*{# the old one while the charts spin. The text is the caption the render #}*/
    /*{# will draw, so the swap changes nothing the eye can see. #}*/
    function _drawCommitted(el, text) {
        var drawn = _drawnValueText(el, el.querySelector('[data-dbt-field]'));
        if (drawn) drawn.textContent = text;
    }

    function _numberBounds(el) {
        function read(attr) {
            var v = parseFloat(el.getAttribute('data-dbt-' + attr));
            return isNaN(v) ? null : v;
        }
        return {min: read('min'), max: read('max'), step: read('step')};
    }

    function _constrainNumber(text, bounds) {
        if (text === '') return text;
        var v = parseFloat(text);
        if (isNaN(v)) return text;
        if (bounds.min !== null && v < bounds.min) v = bounds.min;
        if (bounds.max !== null && v > bounds.max) v = bounds.max;
        if (bounds.step > 0) {
            var base = bounds.min !== null ? bounds.min : 0;
            v = base + Math.round((v - base) / bounds.step) * bounds.step;
            if (bounds.max !== null && v > bounds.max) v -= bounds.step;
            v = parseFloat(v.toFixed(6));
        }
        return String(v);
    }

    /*{# Text entry lifts to a native input rather than being drawn. A caret, #}*/
    /*{# selection, IME, and a mobile keyboard are not things to reimplement in #}*/
    /*{# SVG. The overlay is page-level and native-size — the same rule the #}*/
    /*{# menus follow — so it never inherits the board's scale, and it is #}*/
    /*{# transient, so nothing persistent is positioned against a scaled board. #}*/
    function _bindTextEntry(el, name, inputType) {
        function lift() {
            if (document.querySelector('.dbt-variable-overlay')) return;
            var field = el.querySelector('[data-dbt-field]');

            var input = document.createElement('input');
            input.className = 'dbt-variable-overlay';
            input.type = inputType === 'number' ? 'number' : 'text';
            input.value = el.getAttribute('data-dbt-value') || '';
            var hint = el.getAttribute('data-dbt-placeholder');
            if (hint) input.placeholder = hint;
            /*{# The authored bounds ride on the group; the native input gets #}*/
            /*{# them for its spinner and validity, and commit() enforces them, #}*/
            /*{# since typing past a bound is something a number input allows. #}*/
            var bounds = _numberBounds(el);
            if (inputType === 'number') {
                ['min', 'max', 'step'].forEach(function(key) {
                    if (bounds[key] !== null) input[key] = String(bounds[key]);
                });
            }
            input.style.position = 'fixed';
            /*{# Placed from the field's client rect, and re-placed from it on #}*/
            /*{# every scroll and resize (_repositionOpenPopovers): a fixed #}*/
            /*{# element does not move with the document, so without that the #}*/
            /*{# field slid away under a scroll and the caret stayed behind, #}*/
            /*{# floating over whatever chart had scrolled up beneath it. #}*/
            function place() {
                var fieldRect = (field || el).getBoundingClientRect();
                input.style.left = fieldRect.left + 'px';
                input.style.top = fieldRect.top + 'px';
                input.style.width = fieldRect.width + 'px';
                input.style.height = fieldRect.height + 'px';
                /*{# Corners, type and inset from the drawing it covers, not from #}*/
                /*{# page tokens: those are page pixels, and the drawing is board #}*/
                /*{# units scaled to fit — so a 13px input over a board at 1.7x #}*/
                /*{# shrank the text the moment it was clicked. Everything here #}*/
                /*{# is scaled by the ratio the box itself was. #}*/
                var drawnWidth = field && parseFloat(field.getAttribute('width'));
                if (!(drawnWidth > 0)) return;
                var scale = fieldRect.width / drawnWidth;
                var rx = parseFloat(field.getAttribute('rx'));
                if (rx > 0) input.style.borderRadius = (rx * scale) + 'px';
                var drawnX = parseFloat(field.getAttribute('x'));
                var valueText = _drawnValueText(el, field);
                if (!valueText) return;
                var fontSize = parseFloat(valueText.getAttribute('font-size'));
                if (fontSize > 0) input.style.fontSize = (fontSize * scale) + 'px';
                var family = valueText.getAttribute('font-family');
                if (family) input.style.fontFamily = family;
                var inset = parseFloat(valueText.getAttribute('x')) - drawnX;
                if (inset >= 0) input.style.padding = '0 ' + (inset * scale) + 'px';
            }
            place();
            input.__dbtPlace = place;
            document.body.appendChild(input);
            /*{# While the input is up, the ring stays on the drawn field beneath #}*/
            /*{# it — the same rule, the same box, as a focused drawn control. #}*/
            /*{# The group cannot be :focus while its overlay is, so this marker #}*/
            /*{# is what the stylesheet reads in its place. #}*/
            el.setAttribute('data-dbt-active', '');
            input.focus();
            input.select();

            /*{# The popover's rule, for the control that has no popover. This #}*/
            /*{# click is still bubbling, and a host answers it synchronously — #}*/
            /*{# Cloud reveals the path in its code editor, which focuses the #}*/
            /*{# editor. That blurs this input, and a blur is how it commits and #}*/
            /*{# removes itself, so the field would vanish inside the click that #}*/
            /*{# opened it and a text variable would be untypable in Cloud. The #}*/
            /*{# user clicked the control: in this turn the caret is theirs. A #}*/
            /*{# real click away is a second gesture and still commits below. #}*/
            var liftTurn = true;
            setTimeout(function() { liftTurn = false; }, 0);

            var done = false;
            /*{# `keepCaret` is false when the blur came from another control #}*/
            /*{# taking focus: handing it back here would drag the ring off the #}*/
            /*{# control the user just clicked and onto this one. Enter and #}*/
            /*{# Escape end the edit with nowhere else to be, so those keep it. #}*/
            function commit(save, keepCaret) {
                if (done) return;
                done = true;
                var next = input.value;
                if (inputType === 'number') next = _constrainNumber(next, bounds);
                input.remove();
                el.removeAttribute('data-dbt-active');
                if (keepCaret && el.focus) el.focus();
                if (save && next !== (el.getAttribute('data-dbt-value') || '')) {
                    _drawCommitted(el, next || hint || '');
                    updateVariable(name, next);
                }
            }
            input.__dbtOwner = el;
            input.__dbtDismiss = function() {
                done = true;
                input.remove();
                el.removeAttribute('data-dbt-active');
            };
            input.addEventListener('blur', function(e) {
                /*{# A blur with somewhere to go is another control claiming the #}*/
                /*{# caret; one without is a click on nothing, and the control #}*/
                /*{# this input covers is where focus belongs. #}*/
                var elsewhere = !!e.relatedTarget;
                /*{# The liftTurn grace below is for the *host* focusing its own #}*/
                /*{# UI on the way in. Another drawn control is not that: it is #}*/
                /*{# the user moving on, and reclaiming the caret from it would #}*/
                /*{# leave this input mounted behind the menu they just opened. #}*/
                var toAnotherControl = elsewhere
                    && e.relatedTarget.closest
                    && e.relatedTarget.closest('[data-dbt-variable]')
                    && e.relatedTarget.closest('[data-dbt-variable]') !== el;
                if (!liftTurn || done || toAnotherControl) { commit(true, !elsewhere); return; }
                /*{# The host's own focus call is mid-flight — it blurs this #}*/
                /*{# input on its way in, so focusing back from inside the blur #}*/
                /*{# loses the race and it lands there anyway. Take the caret in #}*/
                /*{# the next turn, once that call has finished. #}*/
                setTimeout(function() { if (!done) input.focus(); }, 0);
            });
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') { e.preventDefault(); commit(true, true); }
                else if (e.key === 'Escape') { e.preventDefault(); commit(false, true); }
            });
        }
        _onValueGesture(el, lift);
        el.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); lift(); }
        });
    }

    /*{# Slider commits in SVG: the track is drawn, so the pointer math happens #}*/
    /*{# in the board's own coordinates and nothing is overlaid at all. #}*/
    function _bindSlider(el, name) {
        var track = el.querySelector('[data-dbt-ornament="slider"]');
        if (!track) return;

        /*{# The drawn track is too thin to aim at, and the thumb takes no #}*/
        /*{# events: this band around it is what a pointer actually lands on. #}*/
        var grip = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        grip.setAttribute('x', track.getAttribute('x'));
        grip.setAttribute('y', parseFloat(track.getAttribute('y')) - 8);
        grip.setAttribute('width', track.getAttribute('width'));
        grip.setAttribute('height', parseFloat(track.getAttribute('height')) + 16);
        grip.setAttribute('fill', 'transparent');
        track.after(grip);

        function bounds() {
            var min = parseFloat(el.getAttribute('data-dbt-min'));
            var max = parseFloat(el.getAttribute('data-dbt-max'));
            var step = parseFloat(el.getAttribute('data-dbt-step'));
            if (isNaN(min) || isNaN(max) || max <= min) return null;
            return {min: min, max: max, step: (isNaN(step) || step <= 0) ? 1 : step};
        }

        /*{# Trim the float noise a fractional step introduces, without #}*/
        /*{# inventing precision the step does not have. #}*/
        function tidy(value) { return parseFloat(value.toFixed(6)); }

        /*{# On the track, not the group: the label sits left of the track, so a #}*/
        /*{# click there maps to a negative fraction, clamps to 0, and silently #}*/
        /*{# re-filters the board to the slider's minimum. Only the track is a #}*/
        /*{# position the user meant to pick. Keys stay on the group, which is #}*/
        /*{# what holds focus. #}*/
        function valueAt(clientX) {
            var b = bounds();
            if (!b) return null;
            var rect = track.getBoundingClientRect();
            if (!rect.width) return null;
            var fraction = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
            var raw = b.min + Math.round((fraction * (b.max - b.min)) / b.step) * b.step;
            return tidy(Math.min(Math.max(raw, b.min), b.max));
        }

        /*{# Move the drawn thumb while the pointer is down. The commit is a #}*/
        /*{# board re-render, so doing one per pointermove would be a render #}*/
        /*{# storm — the thumb follows locally and the value commits on release. #}*/
        function previewAt(value) {
            var b = bounds();
            var thumb = el.querySelector('[data-dbt-ornament="slider-thumb"]');
            if (!b || !thumb) return;
            var rect = track.getBoundingClientRect();
            var svg = el.ownerSVGElement || el.closest('svg');
            if (!rect.width || !svg) return;
            /*{# The track's own drawn x/width, so the preview lands in board #}*/
            /*{# units — the client rect is in page pixels and the board scales. #}*/
            var x = parseFloat(track.getAttribute('x'));
            var w = parseFloat(track.getAttribute('width'));
            if (isNaN(x) || isNaN(w)) return;
            thumb.setAttribute('cx', x + w * ((value - b.min) / (b.max - b.min)));
        }

        /*{# Pointer events rather than click: a slider you cannot drag reads as #}*/
        /*{# broken, and `click` alone gave no drag at all. Capture keeps the #}*/
        /*{# gesture on the slider once it starts, so leaving it mid-drag keeps #}*/
        /*{# sliding instead of dropping the pointer. #}*/
        var dragging = false;
        el.addEventListener('pointerdown', function(e) {
            if (e.target !== grip && e.target !== track) return;
            var value = valueAt(e.clientX);
            if (value === null) return;
            dragging = true;
            if (e.target.setPointerCapture && e.pointerId !== undefined) {
                try { e.target.setPointerCapture(e.pointerId); } catch (err) { /*{# unsupported #}*/ }
            }
            previewAt(value);
        });
        el.addEventListener('pointermove', function(e) {
            if (!dragging) return;
            var value = valueAt(e.clientX);
            if (value !== null) previewAt(value);
        });
        function release(e) {
            if (!dragging) return;
            dragging = false;
            var value = valueAt(e.clientX);
            if (value !== null) updateVariable(name, value);
        }
        el.addEventListener('pointerup', release);
        el.addEventListener('pointercancel', function() { dragging = false; });

        el.addEventListener('keydown', function(e) {
            var b = bounds();
            var current = parseFloat(el.getAttribute('data-dbt-value'));
            if (!b || isNaN(current)) return;
            var next = null;
            if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = current + b.step;
            else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = current - b.step;
            else if (e.key === 'Home') next = b.min;
            else if (e.key === 'End') next = b.max;
            if (next === null) return;
            e.preventDefault();
            updateVariable(name, tidy(Math.min(Math.max(next, b.min), b.max)));
        });
    }

    /*{# ── Daterange preset + calendar machinery ──────────────────────────── #}*/

    /*{# The words this runtime prints come from the board's language, which the #}*/
    /*{# strip carries as `data-dbt-language` (a JSON object keyed by word); the #}*/
    /*{# page cannot fetch the catalog. Stored renders without the attribute are English: #}*/
    /*{# they get the English slice controls_english.js ships. #}*/
    /*{# A word the strip does not carry is English (controls_english.js): a #}*/
    /*{# stored render carries its own release's words, and the runtime is #}*/
    /*{# always current, so it can ask for a word that render predates. #}*/
    function _words(node) {
        var strip = node.closest('[data-dbt-variables]');
        var raw = strip && strip.getAttribute('data-dbt-language');
        return Object.assign({}, window.dbtControlsEnglish, raw ? JSON.parse(raw) : {});
    }

    /*{# The month grid and its range picking are calendar.js's, shared with #}*/
    /*{# the table runtime's date filter. #}*/
    var _cal = window.dbtCalendar;
    var toISO = _cal.toISO;
    var fromISO = _cal.fromISO;
    var _fill = _cal.fill;

    /*{# The captions the render draws for a committed date and date range, #}*/
    /*{# from the same words and short month names it uses, so a pick reads #}*/
    /*{# the same before and after the render that carries it lands. #}*/
    function _dateLabel(d, words, locale) {
        return _fill(words.date, {
            day: d.getDate(), month: locale.shortMonths[d.getMonth()], year: d.getFullYear()
        });
    }

    function _rangeLabel(s, e, words, locale) {
        if (s.getTime() === e.getTime()) return _dateLabel(s, words, locale);
        if (s.getFullYear() === e.getFullYear() && s.getMonth() === e.getMonth()) {
            return _fill(words.range_same_month, {
                start_day: s.getDate(), end_day: e.getDate(),
                month: locale.shortMonths[s.getMonth()], year: s.getFullYear()
            });
        }
        var start = s.getFullYear() === e.getFullYear()
            ? _fill(words.date_no_year, {day: s.getDate(), month: locale.shortMonths[s.getMonth()]})
            : _dateLabel(s, words, locale);
        return _fill(words.range, {start: start, end: _dateLabel(e, words, locale)});
    }

    /*{# Each published group followed by a divider, then 'custom'. A preset #}*/
    /*{# arrives with its label; 'custom' is a word of the language. #}*/
    function buildRail(railEl, words, groups, onPick) {
        railEl.innerHTML = '';
        function button(id, label) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = label;
            btn.dataset.preset = id;
            btn.addEventListener('click', function() { onPick(id); });
            railEl.appendChild(btn);
        }
        groups.forEach(function(group) {
            group.forEach(function(p) { button(p.id, p.label); });
            var div = document.createElement('div');
            div.className = 'dbt-preset-divider';
            railEl.appendChild(div);
        });
        button('custom', words.custom);
    }

    function markRailActive(railEl, presetId) {
        railEl.querySelectorAll('button').forEach(function(b) {
            b.classList.toggle('dbt-preset-active', b.dataset.preset === presetId);
        });
    }

    /*{# Build the daterange popover once at bind time and append to document.body. #}*/
    /*{# Follows _buildSelectPopover: page-level, never inside the board. #}*/
    /*{# Returns the popover element; the calendar state lives in the closure. #}*/
    function _bindDateRange(el, name) {
        var words = _words(el);
        var canUnset = el.getAttribute('data-dbt-can-unset') === 'true';

        /*{# Each preset's [start, end] as the render resolved it. #}*/
        var presetGroups = JSON.parse(el.getAttribute('data-dbt-presets') || '[]');
        var presetRanges = {};
        var presetLabels = {};
        presetGroups.forEach(function(group) {
            group.forEach(function(p) { presetRanges[p.id] = p.range; presetLabels[p.id] = p.label; });
        });
        var locale = JSON.parse(el.getAttribute('data-dbt-time-locale'));
        /*{# A committed preset the menu leaves out still seeds the calendar. #}*/
        var offMenu = JSON.parse(el.getAttribute('data-dbt-preset-off-menu') || 'null');
        if (offMenu) presetRanges[offMenu.id] = offMenu.range;

        var active = null;   /*{# rail entry: a preset id, 'custom', or null #}*/

        var popover = document.createElement('div');
        popover.className = 'dbt-popover dbt-daterange-popover';

        var rail = document.createElement('div');
        rail.className = 'dbt-preset-rail';
        popover.appendChild(rail);

        var calEl = document.createElement('div');
        calEl.className = 'dbt-calendar-area';
        calEl.setAttribute('data-dbt-time-locale', el.getAttribute('data-dbt-time-locale'));
        popover.appendChild(calEl);

        document.body.appendChild(popover);

        /*{# Lingering-popover pattern: auto-commit on second pick; Apply only #}*/
        /*{# closes. Clear is gated on canUnset — a required control must never #}*/
        /*{# offer a path to the empty state. #}*/
        function footer(range) {
            var foot = document.createElement('div');
            foot.className = 'dbt-cal-footer';
            if (canUnset && (range[0] !== null || range[1] !== null)) {
                var clearAction = document.createElement('button');
                clearAction.type = 'button';
                clearAction.className = 'dbt-cal-action';
                clearAction.setAttribute('data-action', 'clear');
                clearAction.textContent = words.clear;
                foot.appendChild(clearAction);
            }
            var applyAction = document.createElement('button');
            applyAction.type = 'button';
            applyAction.className = 'dbt-cal-action dbt-cal-action-primary';
            applyAction.setAttribute('data-action', 'apply');
            applyAction.textContent = words.apply;
            foot.appendChild(applyAction);
            foot.addEventListener('click', function(e) {
                var btn = e.target.closest('[data-action]');
                if (!btn) return;
                var action = btn.getAttribute('data-action');
                if (action === 'clear') {
                    /*{# Terminal, like a select's All row: closed before the #}*/
                    /*{# commit, so the menu is not open when the render lands #}*/
                    /*{# and does not come back with it. open() re-seeds the #}*/
                    /*{# calendar from the committed value, so nothing to reset. #}*/
                    close();
                    _drawCommitted(el, el.getAttribute('data-dbt-unset-label'));
                    updateVariable(name, '');
                } else if (action === 'apply') {
                    close();
                }
            });
            return foot;
        }

        var picker = _cal.rangePicker(calEl, words, {
            onPick: function(range) {
                if (range[0] && range[1]) {
                    active = 'custom';
                    _drawCommitted(el, _rangeLabel(range[0], range[1], words, locale));
                    updateVariable(name, [toISO(range[0]), toISO(range[1])]);
                } else {
                    active = null;
                }
            },
            footer: footer,
            afterBuild: function() { markRailActive(rail, active); },
        });

        function isOpen() { return popover.classList.contains('dbt-popover-open'); }
        function open() {
            /*{# Re-seed every open so a commit from elsewhere (publishCommitted #}*/
            /*{# writes back data-dbt-value) is reflected in the popover's state. #}*/
            var raw = el.getAttribute('data-dbt-value') || '';
            var range = [null, null];
            active = null;
            var parsed = null;
            try { parsed = JSON.parse(raw); } catch (ex) {}
            if (!Array.isArray(parsed) && presetRanges[raw]) {
                active = raw;
                parsed = presetRanges[raw];
            }
            if (Array.isArray(parsed) && parsed.length === 2) {
                /*{# One set end is a value too (the chip reads From/Until), #}*/
                /*{# so either end alone seeds the calendar. #}*/
                range = [fromISO(String(parsed[0])), fromISO(String(parsed[1]))];
                if (!active && range[0] && range[1]) active = 'custom';
            }
            picker.show(range);
            _floatPopover(popover);
            popover.classList.add('dbt-popover-open');
            _positionPopoverFixed(el, popover);
            el.setAttribute('aria-expanded', 'true');
        }
        function close() {
            popover.classList.remove('dbt-popover-open');
            _unfloatPopover(popover);
            el.setAttribute('aria-expanded', 'false');
        }
        el.setAttribute('aria-expanded', 'false');
        /*{# Bubbles, for the reason the select's trigger does. #}*/
        _onValueGesture(el, function() {
            if (isOpen()) close(); else open();
        });
        el.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });

        /*{# DOM rebuilds inside the popover (a day pick, month nav) replace cells, #}*/
        /*{# making the just-clicked node a detached orphan. Without stopPropagation #}*/
        /*{# the document outside-click handler sees the click land on a node whose #}*/
        /*{# trigger.contains(target) is false and closes the popover. #}*/
        popover.addEventListener('click', function(e) { e.stopPropagation(); });

        buildRail(rail, words, presetGroups, function(id) {
            /*{# 'custom' only highlights: the days are picked on the calendar. #}*/
            if (id === 'custom') {
                active = id;
                markRailActive(rail, active);
                return;
            }
            /*{# A preset is terminal, closed before the commit as Clear is. #}*/
            close();
            _drawCommitted(el, presetLabels[id]);
            updateVariable(name, id);
        });

        _registerPopover({trigger: el, popover: popover, isOpen: isOpen, open: open, close: close});
    }

    /*{# Single-date popover: daterange's own calendar in one-endpoint mode. No #}*/
    /*{# preset rail (a preset like "Last 7 days" names a range, not a day) and #}*/
    /*{# no Apply — there is only one endpoint to pick, so a click commits and #}*/
    /*{# closes immediately rather than waiting for a second gesture. #}*/
    function _bindDate(el, name) {
        var words = _words(el);
        var locale = JSON.parse(el.getAttribute('data-dbt-time-locale'));
        var canUnset = el.getAttribute('data-dbt-can-unset') === 'true';

        var selected  = null;
        var viewMonth = new Date();

        var popover = document.createElement('div');
        popover.className = 'dbt-popover';

        var calEl = document.createElement('div');
        calEl.className = 'dbt-calendar-area';
        calEl.setAttribute('data-dbt-time-locale', el.getAttribute('data-dbt-time-locale'));
        popover.appendChild(calEl);

        document.body.appendChild(popover);

        function isOpen() { return popover.classList.contains('dbt-popover-open'); }
        function open() {
            /*{# Re-seed every open so a commit from elsewhere (publishCommitted #}*/
            /*{# writes back data-dbt-value) is reflected in the popover's state. #}*/
            var raw = el.getAttribute('data-dbt-value') || '';
            selected = fromISO(raw);
            viewMonth = selected ? new Date(selected.getFullYear(), selected.getMonth(), 1) : new Date();
            rebuildCalendar();
            _floatPopover(popover);
            popover.classList.add('dbt-popover-open');
            _positionPopoverFixed(el, popover);
            el.setAttribute('aria-expanded', 'true');
        }
        function close() {
            popover.classList.remove('dbt-popover-open');
            _unfloatPopover(popover);
            el.setAttribute('aria-expanded', 'false');
        }
        el.setAttribute('aria-expanded', 'false');
        _onValueGesture(el, function() {
            if (isOpen()) close(); else open();
        });
        el.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });

        /*{# Same reason as _bindDateRange: a month-nav or day-pick rebuild #}*/
        /*{# replaces the clicked node, and without this the document #}*/
        /*{# outside-click handler would see the click land outside the trigger #}*/
        /*{# and close the popover it just landed in. #}*/
        popover.addEventListener('click', function(e) { e.stopPropagation(); });

        function rebuildCalendar() {
            _cal.grid(calEl, words, viewMonth,
                function(dt) { pickDay(dt.getTime()); },
                function(newMonth) { viewMonth = newMonth; rebuildCalendar(); }
            );

            if (selected) {
                var picked = calEl.querySelector('[data-day="' + selected.getTime() + '"]');
                if (picked) picked.classList.add('dbt-selected');
            }

            /*{# Today always renders, unlike Clear (gated on canUnset) — do #}*/
            /*{# not add that gate here, a required field still needs a way #}*/
            /*{# to reach today. No Apply: a single day commits on its own #}*/
            /*{# click, there is no second endpoint to wait for. #}*/
            var footer = document.createElement('div');
            footer.className = 'dbt-cal-footer';
            if (canUnset && selected !== null) {
                var clearAction = document.createElement('button');
                clearAction.type = 'button';
                clearAction.className = 'dbt-cal-action';
                clearAction.setAttribute('data-action', 'clear');
                clearAction.textContent = words.clear;
                footer.appendChild(clearAction);
            }
            var todayAction = document.createElement('button');
            todayAction.type = 'button';
            todayAction.className = 'dbt-cal-action';
            todayAction.setAttribute('data-action', 'today');
            todayAction.textContent = words.today;
            footer.appendChild(todayAction);
            footer.addEventListener('click', function(e) {
                var action = e.target.closest('[data-action]');
                if (!action) return;
                if (action.getAttribute('data-action') === 'clear') {
                    close();
                    _drawCommitted(el, el.getAttribute('data-dbt-unset-label'));
                    updateVariable(name, '');
                } else if (action.getAttribute('data-action') === 'today') {
                    pickDay(_cal.startOfToday().getTime());
                }
            });
            calEl.appendChild(footer);
        }

        function pickDay(timestamp) {
            var dt = new Date(timestamp);
            selected = dt;
            close();
            _drawCommitted(el, _dateLabel(dt, words, locale));
            updateVariable(name, toISO(dt));
        }

        _registerPopover({trigger: el, popover: popover, isOpen: isOpen, open: open, close: close});
    }

    function _bindCheckbox(el, name) {
        function toggle() {
            var checked = el.getAttribute('data-dbt-checked') === 'true';
            updateVariable(name, !checked);
        }
        _onValueGesture(el, toggle);
        el.addEventListener('keydown', function(e) {
            if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); }
        });
    }

    /*{# Give one drawn control its semantics and its handlers. Returns whether #}*/
    /*{# it was newly bound, so mount() can report real work. #}*/
    function bindControl(el) {
        if (el.hasAttribute('data-dbt-bound')) return false;
        var name = el.getAttribute('data-dbt-variable');
        if (!name) return false;
        var input = el.getAttribute('data-dbt-input') || '';

        el.setAttribute('data-dbt-bound', 'true');
        /*{# A control the author gated off gets its semantics but no handlers #}*/
        /*{# and no tab stop: announcing it operable when it is not is worse #}*/
        /*{# than leaving it out of the tab order. #}*/
        if (el.getAttribute('data-dbt-enabled') === 'false') {
            el.setAttribute('aria-disabled', 'true');
            el.setAttribute('role', _ROLE_BY_INPUT[input] || 'button');
            return true;
        }
        el.setAttribute('tabindex', '0');
        el.setAttribute('role', _ROLE_BY_INPUT[input] || 'button');
        if (input === 'checkbox') {
            el.setAttribute('aria-checked', el.getAttribute('data-dbt-checked') || 'false');
        }
        var label = _controlLabel(el);
        if (label) el.setAttribute('aria-label', label);

        if (input === 'checkbox') _bindCheckbox(el, name);
        else if (input === 'select' || input === 'multiselect' || input === 'radio') {
            _bindSelect(el, name);
        } else if (input === 'daterange') _bindDateRange(el, name);
        else if (input === 'date' || input === 'datepicker') _bindDate(el, name);
        else if (input === 'slider' || input === 'range') _bindSlider(el, name);
        else if (input === 'text' || input === 'input' || input === 'textarea'
                 || input === 'number') {
            _bindTextEntry(el, name, input);
        }

        return true;
    }

    /*{# The reset item commits the default for every control drawn in its #}*/
    /*{# strip and drops the legend view of every chart on its board. Hidden #}*/
    /*{# variables are not drawn there, and they carry link context the #}*/
    /*{# viewer cannot see, so they keep their values. #}*/
    function bindReset(el) {
        if (el.hasAttribute('data-dbt-bound')) return;
        el.setAttribute('data-dbt-bound', 'true');
        el.setAttribute('role', 'button');
        el.setAttribute('aria-label', el.textContent.trim());
        /*{# Every filter is at its default: nothing to undo, so no tab stop. #}*/
        if (el.getAttribute('data-dbt-enabled') === 'false') {
            el.setAttribute('aria-disabled', 'true');
            return;
        }
        el.setAttribute('tabindex', '0');
        function reset() {
            var changes = {};
            el.closest('[data-dbt-variables]').querySelectorAll('[data-dbt-variable]')
                .forEach(function(control) {
                    changes[control.getAttribute('data-dbt-variable')] = null;
                });
            Object.keys(activeLegendViews(el.closest('[data-dbt-board]'))).forEach(function(param) {
                changes[param] = null;
            });
            updateVariables(changes);
        }
        el.addEventListener('click', reset);
        el.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); reset(); }
        });
    }

    /*{# Bind every drawn control under `root` (default: the document). #}*/
    /*{# Idempotent — a host re-binds after swapping in a freshly rendered board, #}*/
    /*{# and already-bound controls are skipped. Returns how many were newly #}*/
    /*{# bound, so a caller can tell a real mount from a no-op. #}*/
    function mount(root) {
        var scope = root || document;
        var bound = 0;
        _dropDetachedPopovers();
        _dropOrphanedOverlays();
        /*{# A host hands us the container it just swapped a board into, and that #}*/
        /*{# container often *is* the board — querySelectorAll never matches its #}*/
        /*{# own root, so Cloud bound nothing at all. #}*/
        var boards = Array.prototype.slice.call(
            scope.querySelectorAll('[data-dbt-board]')
        );
        if (scope.matches && scope.matches('[data-dbt-board]')) boards.unshift(scope);
        boards.forEach(function(board) {
            board.querySelectorAll('[data-dbt-variable]').forEach(function(el) {
                if (bindControl(el)) bound++;
            });
            board.querySelectorAll('[data-dbt-reset]').forEach(bindReset);
            /*{# The affordance switch: a stylesheet rule under this class #}*/
            /*{# outranks the render's stroke="none" presentation attribute. #}*/
            /*{# Stroke takes no part in SVG layout, so drawing the borders #}*/
            /*{# cannot move anything — which is why this is a class and not a #}*/
            /*{# re-render. #}*/
            /*{# Both gated on the class: bindControl skips bound controls, but #}*/
            /*{# the hover wiring had no such guard, so a re-mount over an #}*/
            /*{# unchanged board stacked a second listener pair on every control. #}*/
            if (board.querySelector('[data-dbt-variable]')
                && !board.classList.contains('dbt-interactive')) {
                board.classList.add('dbt-interactive');
                setupVariableHoverHighlighting(board);
            }
        });
        _reopenMenus();
        /*{# The hover and table runtimes ship ahead of this one in the same #}*/
        /*{# bundle; a swapped-in board needs binding there too, and a host calls one mount. #}*/
        window.dbtChartHover.mount(scope);
        window.dbtTableRuntime.mount(scope);
        return bound;
    }

    /*{# No resize wiring: a scaled board moves its controls with it, because #}*/
    /*{# they are drawn in the same document. Only an open popover still tracks #}*/
    /*{# the viewport (_repositionOpenPopovers), since a fixed element does not #}*/
    /*{# follow its anchor on its own. #}*/

    if (!_documentWired) {
        window.addEventListener('scroll', _repositionOpenPopovers, true);
        window.addEventListener('resize', _repositionOpenPopovers);
    }

    /*{# Hosts call window.dbtControls.mount(container) after swapping in a board. #}*/
    window.dbtControls = { mount: mount, update: updateVariable };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            mount(document);
            restoreScrollIfSaved();
        });
    } else {
        mount(document);
        restoreScrollIfSaved();
    }
})();
