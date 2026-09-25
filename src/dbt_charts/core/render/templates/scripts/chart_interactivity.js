(function () {
    /*{# Theme facts, read off the board root at mount (data-dbt-font-family, #}*/
    /*{# data-dbt-tooltip-style, data-dbt-hover-emphasis): one static script #}*/
    /*{# for every board, shipped by the host — never templated into a board. #}*/
    /*{# The root carrying them is what makes an svg a board; the host's own #}*/
    /*{# icons are svgs too and stay untouched. Page-level, not per root: a #}*/
    /*{# page showing differently themed boards would need them keyed by root. #}*/
    let DCT_FONT_FAMILY;
    let DCT_TOOLTIP_STYLE;
    let DCT_HOVER_EMPHASIS;

    function readBoardConfig(root) {
        DCT_FONT_FAMILY = root.dataset.dbtFontFamily;
        DCT_TOOLTIP_STYLE = JSON.parse(root.dataset.dbtTooltipStyle);
        DCT_HOVER_EMPHASIS = JSON.parse(root.dataset.dbtHoverEmphasis);
    }

    /*{# Role markers baked by emitters/_tooltip.py's ROLE_HEADER / ROLE_HEADER_SWATCHED / #}*/
    /*{# ROLE_SERIES / ROLE_TOTAL / ROLE_ORDER -- zero-width Unicode format characters, #}*/
    /*{# invisible on screen and not vocalized by screen readers, but valid XML/SVG #}*/
    /*{# attribute content (unlike C0 control chars). Keep these five codepoints in #}*/
    /*{# sync with that module. #}*/
    const ROLE_HEADER = '⁡';
    const ROLE_HEADER_SWATCHED = '⁤';
    const ROLE_SERIES = '⁢';
    const ROLE_TOTAL = '⁣';
    /*{# A precomputed display-order rank for this mark's series, baked once at #}*/
    /*{# emission time from the SAME order that already drives scale.domain / #}*/
    /*{# legend.values (features/structured_tooltip.py) -- present whenever that #}*/
    /*{# order exists, regardless of whether a legend actually renders. Families #}*/
    /*{# with no reordered domain (grouped bar, a numeric/boolean color field) #}*/
    /*{# carry no ROLE_ORDER entry; collectMatchingMarks falls back to reading #}*/
    /*{# the rendered legend's DOM order for those, as before. #}*/
    const ROLE_ORDER = '‌';
    /*{# Emphasis modifier (emitters/_tooltip.py's MUTED), orthogonal to the role #}*/
    /*{# markers above: prefixes a value/total row whose VALUE should render at the #}*/
    /*{# low-contrast label color, not the loud value color -- a companion number #}*/
    /*{# beside a percent lead (pie's raw slice count + grand total). Composes with #}*/
    /*{# a role marker, so it's peeled ahead of the role marker below. #}*/
    const MUTED = '⁠';
    /*{# Brackets a value row's own swatch color (emitters/_tooltip.py's SWATCH): #}*/
    /*{# one block listing several marks' values keys each row to its mark. #}*/
    const SWATCH = '\u200b';
    const HEX_COLOR = /^#[0-9a-fA-F]{3,8}$/;

    /*{# Peels a SWATCH-bracketed color off the front of a row. A row's text can #}*/
    /*{# be warehouse data, so only a well-formed hex counts: anything else (no #}*/
    /*{# closing marker, not a color) is left as text and draws no swatch. #}*/
    function peelSwatch(text) {
        if (text.charAt(0) !== SWATCH) return { swatch: null, text: text };
        const close = text.indexOf(SWATCH, 1);
        const color = close === -1 ? '' : text.slice(1, close);
        if (!HEX_COLOR.test(color)) return { swatch: null, text: text };
        return { swatch: color, text: text.slice(close + 1) };
    }

    /*{# Internal structural dividers (header underline, footer-total rule) are a #}*/
    /*{# fixed hairline, decoupled from the box frame's border.width. The frame #}*/
    /*{# width is 0 on the shadowed themes (the shadow separates the card) but the #}*/
    /*{# header/total lines must persist regardless -- they organize the content, #}*/
    /*{# they aren't the outer edge. Color still comes from the theme (border.color). #}*/
    const DIVIDER_WIDTH = 1;
    // Vertical gap that detaches the overlay reference row (combo target/
    // goal) from the parts+total group in the x-unified bubble (xUnifiedHtml).
    const OVERLAY_DETACH_GAP = 8;

    /*{# Vega's mark-group class. A data mark is always INSIDE one; axes #}*/
    /*{# (role-axis), legends (role-legend), the chart <g>, and the board-root #}*/
    /*{# <svg> never are. #}*/
    const MARK_GROUP_SELECTOR = '.role-mark';

    /*{# Containment decides datum-hood; the label's text never does. The label #}*/
    /*{# shape only decides whether there is anything to SHOW: a role marker, or #}*/
    /*{# a bare "key: value" -- the latter is load-bearing for geo families, #}*/
    /*{# which carry Vega's auto-generated label and no role marker. #}*/
    /*{# Cheap test first: every [aria-label] in the chart runs through here on #}*/
    /*{# each hover transition, so short-circuit on the label before paying for #}*/
    /*{# the ancestor walk. #}*/
    function isDataMark(element) {
        if (!element || typeof element.getAttribute !== 'function') return false;
        const label = element.getAttribute('aria-label');
        if (!label) return false;
        const showable = label.indexOf(ROLE_HEADER) !== -1 || label.indexOf(ROLE_SERIES) !== -1 ||
            label.indexOf(ROLE_HEADER_SWATCHED) !== -1 || label.indexOf(ROLE_TOTAL) !== -1 ||
            label.includes(':');
        return showable && element.closest(MARK_GROUP_SELECTOR) !== null;
    }

    /*{# Returns an ORDERED list of {role, label?, value} entries -- order matters #}*/
    /*{# (header -> series -> dependent values -> footer total), so a plain object #}*/
    /*{# (used pre-hierarchy, when every row rendered identically) can't carry it. #}*/
    function parseAriaLabel(label) {
        if (!label) return [];

        const entries = [];
        const pairs = label.split(';');

        for (const raw of pairs) {
            let pair = raw.trim();
            if (!pair) continue;

            /*{# Peel the emphasis modifier ahead of the role marker it composes #}*/
            /*{# with -- only value/total rows carry it (header/series never do). #}*/
            let muted = false;
            if (pair.charAt(0) === MUTED) { muted = true; pair = pair.slice(1); }

            const marker = pair.charAt(0);
            if (marker === ROLE_HEADER || marker === ROLE_HEADER_SWATCHED) {
                entries.push({
                    role: 'header',
                    swatch: marker === ROLE_HEADER_SWATCHED,
                    value: pair.slice(1),
                });
                continue;
            }
            if (marker === ROLE_SERIES) {
                const peeled = peelSwatch(pair.slice(1));
                entries.push({ role: 'series', value: peeled.text, swatch: peeled.swatch });
                continue;
            }
            if (marker === ROLE_ORDER) {
                entries.push({ role: 'order', value: pair.slice(1) });
                continue;
            }

            const isTotal = marker === ROLE_TOTAL;
            const peeled = peelSwatch(isTotal ? pair.slice(1) : pair);
            const rest = peeled.text;
            const swatch = peeled.swatch;
            const colonIndex = rest.indexOf(':');
            if (colonIndex === -1) continue;

            const key = rest.substring(0, colonIndex).trim();
            const value = rest.substring(colonIndex + 1).trim();
            /*{# A muted value may go unlabeled: a gray second line under the row above. #}*/
            if (!key && !muted) continue;

            entries.push({ role: isTotal ? 'total' : 'value', label: key, value: value, muted: muted, swatch: swatch });
        }

        return entries;
    }

    /*{# Row cap for the x-unified bubble (grouped bar / stacked bar / aligned #}*/
    /*{# multi-series line): shows at most MAX_XUNIFIED_ROWS - 1 individual rows #}*/
    /*{# plus one "+N more" remainder row (MAX_XUNIFIED_ROWS lines total). #}*/
    const MAX_XUNIFIED_ROWS = 8;
    /*{# Past this many exact-identity matches the bubble would be unreadably #}*/
    /*{# tall -- fall back to the single-mark tooltip instead of expanding. #}*/
    /*{# Mirrors the theme's default legend.symbol_limit (defaults/themes/_base.yaml) #}*/
    /*{# as the same "this many distinct series stops being legible" cutoff. #}*/
    const XUNIFIED_ABANDON_THRESHOLD = 20;

    function markHeaderEntry(entries) {
        for (let i = 0; i < entries.length; i++) {
            if (entries[i].role === 'header') return entries[i];
        }
        return null;
    }

    /*{# ALL header-role entries, in aria-label order. Every family but heatmap #}*/
    /*{# carries exactly one (x, or the color dim); heatmap's compound [x, y] #}*/
    /*{# identity carries two -- both must contribute to the match key below, or #}*/
    /*{# grouping-by-header would collapse every heatmap cell sharing just the x #}*/
    /*{# value into one (wrong) bubble. #}*/
    function headerEntries(entries) {
        return entries.filter(function (e) { return e.role === 'header'; });
    }

    /*{# The identity to group marks by -- joins every header entry's value. #}*/
    /*{# Degenerates to a single value for every family but heatmap, so this is #}*/
    /*{# not a behavior change for the single-header case. #}*/
    function headerKey(entries) {
        return headerEntries(entries).map(function (e) { return e.value; }).join('|');
    }

    function markSeriesEntry(entries) {
        for (let i = 0; i < entries.length; i++) {
            if (entries[i].role === 'series') return entries[i];
        }
        return null;
    }

    function markOrderEntry(entries) {
        for (let i = 0; i < entries.length; i++) {
            if (entries[i].role === 'order') return entries[i];
        }
        return null;
    }

    /*{# The baked rank is the only genuinely numeric entry the aria-label carries. #}*/
    function orderRank(entry) {
        const n = parseInt(entry.value, 10);
        return isNaN(n) ? Infinity : n;
    }

    function findTotalEntry(matches) {
        for (let i = 0; i < matches.length; i++) {
            const total = matches[i].entries.find(function (e) { return e.role === 'total'; });
            if (total) return total;
        }
        return null;
    }

    /*{# The chart's canonical series order, read from the color legend's #}*/
    /*{# DOM order (VL renders labels in the color-scale domain order). #}*/
    /*{# Prefers `data-dbt-series` -- the raw, untruncated value stamped by #}*/
    /*{# converters/chart.py's SVG post-process pass -- falling back to #}*/
    /*{# trimmed textContent when unstamped (a scenegraph probe failure). #}*/
    /*{# Returns a {seriesValue: index} map, or an empty map when there's no #}*/
    /*{# legend (e.g. endpoint-labeled lines) -- in which case matches #}*/
    /*{# keep their natural order. #}*/
    function legendSeriesOrder(svg) {
        const order = {};
        let i = 0;
        svg.querySelectorAll('.role-legend-label').forEach(function (el) {
            const name = el.getAttribute('data-dbt-series') || (el.textContent || '').trim();
            if (name && !(name in order)) { order[name] = i++; }
        });
        return order;
    }

    /*{# Which PANEL a mark sits in for a small-multiples facet: the direct #}*/
    /*{# child of the shared `.role-scope.cell` group that contains it. #}*/
    /*{# `multiples:` (row facet, column facet, or grid) always emits exactly #}*/
    /*{# ONE such cell wrapping every panel, each an otherwise-unclassed #}*/
    /*{# sibling `<g>` -- identical shape for row and column facets and for #}*/
    /*{# every cartesian family. A non-faceted chart -- and a layered, combo, #}*/
    /*{# or dual-axis chart, whose wrappers are named `role-scope concat_0_group` #}*/
    /*{# / `role-scope concat_0_layer_N_pathgroup` instead -- never emits a #}*/
    /*{# `.role-scope.cell`, so the walk finds none and this degenerates to #}*/
    /*{# `chartEl`: the panel IS the whole chart, today's behavior. #}*/
    function panelScope(mark, chartEl) {
        const cell = mark.closest('.role-scope.cell');
        if (!cell) return chartEl;
        let panel = mark;
        while (panel.parentNode !== cell) { panel = panel.parentNode; }
        return panel;
    }

    /*{# The reverse of panelScope(): which panel the cursor sits over, by #}*/
    /*{# GEOMETRY -- containment on the mousemove TARGET does not work. #}*/
    /*{# Every panel's own background is a `<path class="background">` with #}*/
    /*{# no `fill` of its own, nested under an ancestor `<g fill="none">` #}*/
    /*{# (verified against a live render: `dbt-charts/tests/visual/goldens/ #}*/
    /*{# fixtures/facet-panel-axis-title.svg`) -- so it inherits `fill: #}*/
    /*{# none`, and its gridlines carry `pointer-events="none"` on top of #}*/
    /*{# that. Nothing inside a panel's own empty space is ever painted, so #}*/
    /*{# the topmost element a mousemove there actually targets is the #}*/
    /*{# chart-wide background rect -- a sibling of the `<g fill="none">` #}*/
    /*{# that wraps `.role-scope.cell`, never #}*/
    /*{# a descendant of any one panel. Containment therefore never matches #}*/
    /*{# from inside a panel, which is exactly where the bounded proximity #}*/
    /*{# snap (nearestDataMark's `maxDistance` branch, below) needs to run. #}*/
    /*{# Walking each panel's own `getBoundingClientRect()` against the #}*/
    /*{# cursor's screen point sidesteps the DOM question entirely: it does #}*/
    /*{# not matter what got hit, only where the cursor is. Still returns #}*/
    /*{# null for the gutter between panels -- no panel's rect covers it -- #}*/
    /*{# so the caller's contract is unchanged: fall through to hiding the #}*/
    /*{# tooltip rather than guess which panel was meant. Degenerates to #}*/
    /*{# `chartEl` for a non-faceted chart, same as panelScope(). #}*/
    function panelAtPoint(chartEl, clientX, clientY) {
        const cell = chartEl.querySelector('.role-scope.cell');
        if (!cell) return chartEl;
        for (let i = 0; i < cell.children.length; i++) {
            const panel = cell.children[i];
            const rect = panel.getBoundingClientRect();
            if (clientX >= rect.left && clientX <= rect.right
                && clientY >= rect.top && clientY <= rect.bottom) {
                return panel;
            }
        }
        return null;
    }

    /*{# Groups every data mark in the chart whose IDENTITY (header value) #}*/
    /*{# EXACTLY matches the hovered mark's -- string equality only, no #}*/
    /*{# nearest-point tracking. A mark with no header (rare, deferred families) #}*/
    /*{# or a mismatched x never groups; the caller's single-mark path covers it. #}*/

    /*{# Dedup key is (header, series), not the raw aria-label text, so a datum #}*/
    /*{# that more than one mark announces still counts as one row. #}*/

    /*{# Rows are then ordered to match the chart's canonical series order, never #}*/
    /*{# the marks' DOM/stacking order -- so the tooltip reads top-to-bottom in #}*/
    /*{# that order. The per-mark baked rank (ROLE_ORDER) wins when present -- #}*/
    /*{# it's set directly from the same order authority regardless of whether a #}*/
    /*{# legend actually renders (a line with endpoint labels instead of a #}*/
    /*{# legend has no `.role-legend-label` DOM to read). Falls back to the #}*/
    /*{# rendered legend's DOM order otherwise. Series with neither sort last, #}*/
    /*{# stably. #}*/
    function collectMatchingMarks(svg, hoveredMark, hoveredEntries) {
        if (!markHeaderEntry(hoveredEntries)) return [{ mark: hoveredMark, entries: hoveredEntries }];
        const identity = headerKey(hoveredEntries);

        /*{# Scope grouping to the hovered mark's OWN chart. The runtime is #}*/
        /*{# bound once at the board-root <svg>, but each chart is a #}*/
        /*{# nested standalone <svg>; querying the whole board would collect #}*/
        /*{# same-identity marks from OTHER charts (e.g. two `x: month` charts) and #}*/
        /*{# contaminate the bubble with foreign rows / wrong values. Mirrors the #}*/
        /*{# .dbt-chart scoping nearestDataMark already uses. #}*/
        const chartScope = hoveredMark.closest('.dbt-chart') || svg;
        /*{# Narrower still to the hovered mark's own facet PANEL: identical #}*/
        /*{# x-values repeat across panels, so an unscoped sweep would pull a #}*/
        /*{# same-header mark from a sibling panel into this bubble. The #}*/
        /*{# legend stays scoped to the whole CHART below (`legendSeriesOrder`) #}*/
        /*{# -- a facet shares one legend outside every panel. #}*/
        const matchScope = panelScope(hoveredMark, chartScope);

        const seen = {};
        const matches = [];
        matchScope.querySelectorAll('[aria-label]').forEach(function (candidate) {
            if (!isDataMark(candidate)) return;
            const entries = parseAriaLabel(candidate.getAttribute('aria-label'));
            if (!markHeaderEntry(entries) || headerKey(entries) !== identity) return;

            const series = markSeriesEntry(entries);
            const key = identity + '|' + (series ? series.value : '');
            if (seen[key]) return;
            seen[key] = true;
            matches.push({ mark: candidate, entries: entries });
        });

        const order = legendSeriesOrder(chartScope);
        const hasLegendOrder = Object.keys(order).length > 0;
        const hasBakedOrder = matches.some(function (m) { return markOrderEntry(m.entries); });
        if (hasLegendOrder || hasBakedOrder) {
            matches.forEach(function (m, idx) { m._i = idx; });
            matches.sort(function (a, b) {
                const oa = markOrderEntry(a.entries);
                const ob = markOrderEntry(b.entries);
                const sa = markSeriesEntry(a.entries);
                const sb = markSeriesEntry(b.entries);
                /*{# The rendered legend wins where a row appears in it: the bubble #}*/
                /*{# then reads in the order the reader already sees. A baked rank #}*/
                /*{# covers rows no legend lists (an endpoint-labeled chart). #}*/
                const ka = sa && sa.value in order ? order[sa.value] : (oa ? orderRank(oa) : Infinity);
                const kb = sb && sb.value in order ? order[sb.value] : (ob ? orderRank(ob) : Infinity);
                return ka !== kb ? ka - kb : a._i - b._i;
            });
        }
        return matches;
    }

    /*{# Splits matches into the rows to render individually and the rows to #}*/
    /*{# fold into a "+N more" remainder -- always keeping the hovered mark's #}*/
    /*{# own row visible, even when it would otherwise fall past the cap. #}*/
    function buildRowPlan(matches, hoveredMark) {
        if (matches.length <= MAX_XUNIFIED_ROWS) {
            return { shown: matches, remainder: [] };
        }
        let hoveredIndex = -1;
        for (let i = 0; i < matches.length; i++) {
            if (matches[i].mark === hoveredMark) { hoveredIndex = i; break; }
        }
        const visibleCount = MAX_XUNIFIED_ROWS - 1;
        let shown = matches.slice(0, visibleCount);
        if (hoveredIndex >= visibleCount) {
            shown = shown.slice(0, visibleCount - 1).concat([matches[hoveredIndex]]);
        }
        const shownMarks = shown.map(function (m) { return m.mark; });
        const remainder = matches.filter(function (m) { return shownMarks.indexOf(m.mark) === -1; });
        return { shown: shown, remainder: remainder };
    }

    /*{# Screen-pixel snap radius for the proximity fallback below. Deliberately #}*/
    /*{# in SCREEN px, not board units: a board declares a fixed viewBox and is #}*/
    /*{# then scaled to whatever width the host gives it, so a hit target sized #}*/
    /*{# at render time shrinks with the page. The invisible per-datum target #}*/
    /*{# emitters/_layers.py adds to line/area is 8.66 board units, which is a #}*/
    /*{# comfortable 8.7px at 1:1 and a 4px pinprick on a board scaled to half #}*/
    /*{# width -- and scatter has no such target at all, so its hit area is the #}*/
    /*{# visible dot. Snapping here fixes both, and cannot shrink. #}*/
    const HOVER_SNAP_RADIUS_PX = 24;

    /*{# `maxDistance` (screen px) bounds the search; omitted means unbounded. #}*/
    /*{# `accept`, when given, is a further predicate on candidates -- the #}*/
    /*{# bounded proximity snap passes `isSnappableMark`, which keeps this away #}*/
    /*{# from chart furniture (a value label, a series-label rail's legend #}*/
    /*{# text) that carries an aria-label but is nowhere near a real datum. #}*/
    function nearestDataMark(chart, x, y, maxDistance, accept) {
        let nearest = null;
        let nearestDistance = maxDistance === undefined ? Infinity : maxDistance * maxDistance;

        chart.querySelectorAll('[aria-label]').forEach(function (mark) {
            if (!isDataMark(mark)) return;
            if (accept && !accept(mark)) return;

            const rect = mark.getBoundingClientRect();
            const dx = Math.max(rect.left - x, 0, x - rect.right);
            const dy = Math.max(rect.top - y, 0, y - rect.bottom);
            const distance = dx * dx + dy * dy;
            if (distance < nearestDistance) {
                nearest = mark;
                nearestDistance = distance;
            }
        });

        return nearest;
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';

        const div = document.createElement('div');
        div.textContent = String(value);
        return div.innerHTML;
    }

    function formatFieldName(key) {
        return escapeHtml(key
            .replace(/_/g, ' ')
            .replace(/-/g, ' ')
            .replace(/\b\w/g, function (c) { return c.toUpperCase(); }));
    }

    function formatValue(value) {
        if (value === null || value === undefined) return "__DCT_NULL_DISPLAY__";
        return escapeHtml(value);
    }

    function ensureState() {
        if (window.__dbtChartsChartHoverState) {
            return window.__dbtChartsChartHoverState;
        }

        const ts = DCT_TOOLTIP_STYLE;
        const tooltip = document.createElement('div');
        tooltip.className = 'dbt-tooltip';
        tooltip.setAttribute('role', 'tooltip');
        tooltip.style.position = 'fixed';
        tooltip.style.display = 'none';
        tooltip.style.background = ts.background;
        tooltip.style.padding = ts.padding.top + 'px ' + ts.padding.right + 'px ' + ts.padding.bottom + 'px ' + ts.padding.left + 'px';
        tooltip.style.borderRadius = ts.border.radius + 'px';
        tooltip.style.fontSize = ts.font.size + 'px';
        tooltip.style.lineHeight = String(ts.lineHeight);
        tooltip.style.pointerEvents = 'none';
        tooltip.style.zIndex = '10000';
        /*{# Size to content, capped at maxWidth. A fixed-position box is #}*/
        /*{# shrink-to-fit by default, but that only holds when it's laid out #}*/
        /*{# against the viewport; inside an embedding host's iframe the box can #}*/
        /*{# stretch toward maxWidth, ballooning the x-unified grid's 1fr name #}*/
        /*{# column and the hovered-row highlight with it. max-content pins the #}*/
        /*{# width to the content in every host, so the highlight stays tight. #}*/
        tooltip.style.width = 'max-content';
        tooltip.style.maxWidth = ts.maxWidth + 'px';
        tooltip.style.boxShadow = ts.shadow.visible ? '0 10px 25px rgba(15, 23, 42, 0.25)' : 'none';
        tooltip.style.backdropFilter = 'blur(4px)';
        tooltip.style.border = ts.border.width + 'px solid ' + ts.border.color;
        tooltip.style.fontFamily = DCT_FONT_FAMILY;
        if (document.body) {
            document.body.appendChild(tooltip);
        }

        window.__dbtChartsChartHoverState = {
            activeMark: null,
            tooltip: tooltip,
            /*{# Hover-emphasis state is just the list of elements currently #}*/
            /*{# dimmed, so clearing is "put back what I touched" and nothing #}*/
            /*{# else -- no geometry, no inserted nodes, no cache key. #}*/
            emphasis: { dimmed: [], drawn: [] },
        };

        return window.__dbtChartsChartHoverState;
    }

    function positionTooltip(state, x, y) {
        const padding = 12;
        const rect = state.tooltip.getBoundingClientRect();
        let left = x + padding;
        let top = y + padding;

        if (left + rect.width > window.innerWidth - padding) {
            left = x - rect.width - padding;
        }

        if (top + rect.height > window.innerHeight - padding) {
            top = y - rect.height - padding;
        }

        state.tooltip.style.left = Math.max(padding, left) + 'px';
        state.tooltip.style.top = Math.max(padding, top) + 'px';
    }

    function hideTooltip(state) {
        state.activeMark = null;
        state.tooltip.style.display = 'none';
        clearHoverEmphasis(state);
    }

    /*{# ---------------------------------------------------------------- #}*/
    /*{# Hover emphasis (bar family). Hovering a bar recedes the marks the #}*/
    /*{# tooltip is NOT describing; the hovered ones are left alone. #}*/
    /*{#                                                                  #}*/
    /*{# Receding writes a mark's fill and stroke as a SOLID color: its own #}*/
    /*{# paint mixed toward the chart's background. Arithmetically that is #}*/
    /*{# the composite the mark would show at partial opacity over the #}*/
    /*{# background -- `0.7 * mark + 0.3 * background` -- so it is the same #}*/
    /*{# pixel a background-colored veil over the plot would produce. It is #}*/
    /*{# not an opacity write, deliberately: a faded bar shows the gridlines #}*/
    /*{# straight through it, and every repair for that is heavy -- an opaque #}*/
    /*{# twin painted under each bar at render time, or a veil with every #}*/
    /*{# element that must stay lit cloned back above it at reconstructed #}*/
    /*{# geometry. A solid color needs neither; what lies behind the mark #}*/
    /*{# never enters into it. #}*/
    /*{#                                                                  #}*/
    /*{# The browser does the mixing (CSS color-mix), so no color parser #}*/
    /*{# lives here: Vega's hex, an author's named color and an rgb() #}*/
    /*{# literal all mix alike. Nothing here moves a node, so identity #}*/
    /*{# mismatches are COSMETIC (something stays lit that could have #}*/
    /*{# receded), never structural (a clone landing at the wrong offset). #}*/
    /*{# That is the whole robustness argument for this shape. #}*/

    /*{# Which marks may recede, read off what Vega-Lite already says about #}*/
    /*{# each one rather than from a stamp of our own: VL names every mark's #}*/
    /*{# family in the SVG, and writes its translucency as an attribute. #}*/
    /*{# Admitting a family is one more entry here, after its own browser #}*/
    /*{# review. This list lives only in the runtime, so admitting a family #}*/
    /*{# never touches rendered SVG, and the goldens stay untouched -- unlike #}*/
    /*{# `data-dbt-magnitude-colored` and `data-dbt-value-label` below, which #}*/
    /*{# ARE stamped onto the SVG for this feature to read. #}*/
    /*{#                                                                  #}*/
    /*{# Vega is not consistent about the strings -- a bar mark is "bar" #}*/
    /*{# (which also covers a histogram's bins), a pie or donut slice is #}*/
    /*{# "arc mark", and a heatmap's cell is "rect mark" -- so they are #}*/
    /*{# matched literally rather than derived from a pattern. This is the #}*/
    /*{# GEOMETRY half of the gate only ("is receding legible for this mark's #}*/
    /*{# shape") -- big filled marks recede; a thin overlay line is neither #}*/
    /*{# admitted here nor covered by the PALETTE half below, since a line's #}*/
    /*{# own hover treatment (the drop-line marker) is reviewed separately. #}*/
    /*{# Whether recedING is actually SAFE for a given chart -- whether its #}*/
    /*{# color encodes identity or magnitude -- is the isMagnitudeColored() #}*/
    /*{# check further down, and the two are independent: a heatmap cell is #}*/
    /*{# geometry-eligible here regardless of which palette it uses. #}*/
    const RECEDE_ROLES = ['bar', 'arc mark', 'rect mark'];

    function isRecedableMark(el) {
        return RECEDE_ROLES.indexOf(el.getAttribute('aria-roledescription')) !== -1;
    }

    /*{# A mark translucent at rest keeps its paint: its color on screen #}*/
    /*{# already includes whatever lies behind it, which a blend toward the #}*/
    /*{# background cannot know. opacity="0" is the degenerate case -- Vega's #}*/
    /*{# marker for an invisible hit target (bar_hover_band.py's bands), whose #}*/
    /*{# paint is never on screen at all. #}*/
    function isOpaqueAtRest(el) {
        return ['opacity', 'fill-opacity'].every(function (attr) {
            const v = el.getAttribute(attr);
            return v === null || parseFloat(v) >= 1;
        });
    }

    /*{# The color receded marks mix toward: the chart's own background, #}*/
    /*{# read off the document rather than shipped beside it. Vega paints a #}*/
    /*{# view's `background` as the first child <rect> of the <svg> it #}*/
    /*{# renders, so the ground is already stacked next to the marks -- per #}*/
    /*{# chart, which is what a chart-local background override needs. No #}*/
    /*{# rect means no ground to mix toward, and nothing recedes. #}*/
    function chartBackground(mark) {
        const view = mark.closest('svg');
        const ground = view && view.firstElementChild;
        if (!ground || ground.tagName.toLowerCase() !== 'rect') return null;
        const fill = ground.getAttribute('fill');
        return isColor(fill) ? fill : null;
    }

    /*{# The marks that may recede: the bars/arcs, plus each one's OWN value #}*/
    /*{# label, which must recede WITH its mark or it is left printed at full #}*/
    /*{# strength over a receded one. `data-dbt-value-label` (stamped by #}*/
    /*{# converters/chart.py on the text-mark GROUP holding a mark's own #}*/
    /*{# printed value -- never on a pie/donut center total or outside label, #}*/
    /*{# which are built directly in emitters/pie.py and never go through the #}*/
    /*{# value-label feature) is what says a text mark is reachable here at #}*/
    /*{# all; which byte-identical aria-label ties it to its own bar/arc, #}*/
    /*{# with no sublayer bookkeeping needed. Nothing about a solid blend #}*/
    /*{# needs a bar specifically -- any solidly painted mark recedes the #}*/
    /*{# same way. #}*/
    /*{#                                                                       #}*/
    /*{# Stated as what may recede rather than what may not, matching the #}*/
    /*{# rest of this function: a bar chart's series-label rail renders its #}*/
    /*{# legend as TEXT marks with the same `role-mark` ancestor and the same #}*/
    /*{# "key: value" aria-label shape as a real datum, and a baseline rule #}*/
    /*{# carries Vega's own "revenue: 0" -- neither carries #}*/
    /*{# `data-dbt-value-label`, so neither is reachable here, with no #}*/
    /*{# exclusion list needed to say so. #}*/
    function recedableMarks(chartEl) {
        const labeled = chartEl.querySelectorAll('[aria-label]');
        const bars = [];
        const barLabels = {};
        labeled.forEach(function (el) {
            if (!isRecedableMark(el) || !el.closest(MARK_GROUP_SELECTOR)) return;
            bars.push(el);
            barLabels[el.getAttribute('aria-label')] = true;
        });
        const twins = [];
        labeled.forEach(function (el) {
            if (isRecedableMark(el) || !el.closest(MARK_GROUP_SELECTOR)) return;
            if (!el.closest('[data-dbt-value-label]')) return;
            if (barLabels[el.getAttribute('aria-label')]) twins.push(el);
        });
        return bars.concat(twins);
    }

    /*{# Color that encodes MAGNITUDE (a sequential/diverging scale) rather #}*/
    /*{# than identity is excluded: a receded mark's color IS a different #}*/
    /*{# value on that scale, and the color legend sits outside the plot and #}*/
    /*{# never recedes with it, so every demoted mark stops matching its own #}*/
    /*{# legend. Checked per CHART, not by family name -- a bar colored by a #}*/
    /*{# continuous field is legal and hits exactly this, and a heatmap #}*/
    /*{# colored by a categorical field is equally legal and does NOT hit it. #}*/
    /*{#                                                                  #}*/
    /*{# Read off a fact core/render/chart/rendering.py stamps onto the #}*/
    /*{# `.dbt-chart` wrapper (data-dbt-magnitude-colored), not off the #}*/
    /*{# rendered legend: a rendered gradient swatch is DOM guesswork that #}*/
    /*{# misses a chart with its legend hidden (style.legend.visible: false) #}*/
    /*{# and, on a small-multiples facet, a chart whose one shared legend #}*/
    /*{# renders outside every panel by default -- either way, no gradient #}*/
    /*{# node ever reaches the DOM this function can see, so the old sniff #}*/
    /*{# wrongly called the chart categorical. The compile-time fact behind #}*/
    /*{# the stamp is `ResolvedChart.resolved_channels["color"]` -- whether #}*/
    /*{# its mode is a continuous ramp, plus heatmap's own case (a bare #}*/
    /*{# numeric `color:` field, which resolves to the same "series" mode a #}*/
    /*{# categorical field gets, yet still paints a magnitude gradient by #}*/
    /*{# default) -- computed once, correctly, regardless of what the #}*/
    /*{# legend renders. #}*/
    function isMagnitudeColored(chartEl) {
        return chartEl.hasAttribute('data-dbt-magnitude-colored');
    }

    /*{# The two paints a mark can carry. Both recede: a bar's border left at #}*/
    /*{# full strength around a receded fill outlines it harder than the #}*/
    /*{# hovered bar, which reads as the opposite emphasis. #}*/
    const RECEDABLE_PAINT = ['fill', 'stroke'];

    /*{# 'none' is Vega's explicit not-painted value and a url() is a pattern #}*/
    /*{# or gradient; neither is a color, so neither can mix. 'transparent' #}*/
    /*{# is a color, but mixing toward it yields an alpha -- the exact #}*/
    /*{# write this mechanism exists to avoid -- so a see-through ground is #}*/
    /*{# no ground at all. #}*/
    function isColor(paint) {
        return !!paint && paint !== 'none' && paint !== 'transparent' && paint.indexOf('url(') !== 0;
    }

    function clearHoverEmphasis(state) {
        state.emphasis.dimmed.forEach(function (el) {
            RECEDABLE_PAINT.forEach(function (prop) { el.style.removeProperty(prop); });
        });
        state.emphasis.dimmed = [];
        state.emphasis.drawn.forEach(function (el) {
            if (el.parentNode) el.parentNode.removeChild(el);
        });
        state.emphasis.drawn = [];
    }

    /*{# `keptLabels` is a set of raw aria-label strings, not nodes. Vega #}*/
    /*{# stamps every sublayer of one datum with the BYTE-IDENTICAL label, so #}*/
    /*{# matching on the string keeps a bar, its own value label, and its #}*/
    /*{# invisible hover band lit together with no sublayer bookkeeping at #}*/
    /*{# all -- the case that needed a repeat-count heuristic when marks had #}*/
    /*{# to be cloned above a veil instead. #}*/
    function applyHoverEmphasis(state, mark, keptLabels) {
        clearHoverEmphasis(state);
        if (!DCT_HOVER_EMPHASIS.visible) return;
        const chartEl = mark.closest('.dbt-chart');
        if (!chartEl || isMagnitudeColored(chartEl)) return;
        const background = chartBackground(mark);
        if (!background) return;
        const share = Math.round(DCT_HOVER_EMPHASIS.opacity * 100) + '%';

        /*{# Candidates are scoped to the hovered mark's own facet PANEL, not #}*/
        /*{# the whole chart -- a small-multiples facet packs every panel's #}*/
        /*{# marks under one shared `.role-scope.cell` (see panelScope()), so #}*/
        /*{# an unscoped sweep would recede every mark in every OTHER panel #}*/
        /*{# too, not just the ones the tooltip isn't describing. #}*/
        const dimmed = [];
        recedableMarks(panelScope(mark, chartEl)).forEach(function (el) {
            if (keptLabels[el.getAttribute('aria-label')] || !isOpaqueAtRest(el)) return;
            let touched = false;
            RECEDABLE_PAINT.forEach(function (prop) {
                const paint = el.getAttribute(prop);
                if (!isColor(paint)) return;
                el.style.setProperty(prop, 'color-mix(in srgb, ' + paint + ' ' + share + ', ' + background + ')');
                touched = true;
            });
            if (touched) dimmed.push(el);
        });
        state.emphasis.dimmed = dimmed;
    }

    /*{# ---------------------------------------------------------------- #}*/
    /*{# POC -- hover markers for the point families (line / area / scatter). #}*/
    /*{#                                                                  #}*/
    /*{# The bar family recedes what is NOT hovered, because a bar is big #}*/
    /*{# enough that removing ink from its neighbors reads as emphasis. A #}*/
    /*{# datum on a line is a few pixels, so the same move has nothing to #}*/
    /*{# act on -- the emphasis has to ADD ink at the datum instead. #}*/
    /*{#                                                                  #}*/
    /*{# Nothing here reconstructs geometry. Vega already emits a per-datum #}*/
    /*{# symbol for line/area (emitters/_layers.py's invisible hover target, #}*/
    /*{# opacity 0) and a visible one for scatter, each positioned by its own #}*/
    /*{# `transform` -- so the datum's coordinates are read off the DOM, never #}*/
    /*{# derived from a scale. Same for the drop line's foot: the x-axis group #}*/
    /*{# carries the baseline in its own transform. #}*/
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const MARKER_DOT_RADIUS = 5;
    const MARKER_RING_GAP = 4;
    const MARKER_STROKE_WIDTH = 2;

    function svgNode(name, attrs) {
        const el = document.createElementNS(SVG_NS, name);
        Object.keys(attrs).forEach(function (k) { el.setAttribute(k, String(attrs[k])); });
        /*{# Without this the marker becomes the mousemove target, the handler #}*/
        /*{# resolves no mark, and the tooltip hides itself the instant it draws. #}*/
        el.setAttribute('pointer-events', 'none');
        return el;
    }

    /*{# Where `el`'s own origin lands in `container`'s coordinate system. #}*/
    /*{# Via screen space, so it holds for any nesting or intermediate #}*/
    /*{# transform without this code knowing the chart's structure. A Vega #}*/
    /*{# symbol's `d` is centered on that origin, so for a point this IS the #}*/
    /*{# datum; for the x-axis group it is the baseline. #}*/
    function localOriginIn(el, container) {
        const from = el.getScreenCTM();
        const to = container.getScreenCTM();
        if (!from || !to) return null;
        return new DOMPoint(0, 0).matrixTransform(to.inverse().multiply(from));
    }

    /*{# A step-band datum's other half: apply_step_band() (step_band.py) #}*/
    /*{# doubles every row into a left/right-edge symbol pair with #}*/
    /*{# byte-identical aria-labels, emitted as immediate DOM siblings -- Vega #}*/
    /*{# itself never draws a mid-band symbol. Restricted to `previousElementSibling` #}*/
    /*{# / `nextElementSibling` (never a chart-wide label search) so a genuinely #}*/
    /*{# duplicate label elsewhere in the same symbol group -- a real repeated #}*/
    /*{# data value -- is never mistaken for a band pair. #}*/
    function stepBandPartner(point) {
        const label = point.getAttribute('aria-label');
        if (!label) return null;
        const prev = point.previousElementSibling;
        if (prev && prev.getAttribute && prev.getAttribute('aria-label') === label) return prev;
        const next = point.nextElementSibling;
        if (next && next.getAttribute && next.getAttribute('aria-label') === label) return next;
        return null;
    }

    /*{# `localOriginIn`, but averaged with a step-band partner when one #}*/
    /*{# exists. Without this, hovering a plateau's middle has the caller's #}*/
    /*{# own proximity snap (nearestDataMark) pick whichever edge symbol is #}*/
    /*{# spatially closer, so the drawn marker visibly flips between the two #}*/
    /*{# edges as the cursor crosses. Averaging both edges' screen origins #}*/
    /*{# gives the band's true midpoint regardless of which edge the caller #}*/
    /*{# passed in -- a pure DOM read, no tagging at emission time. Every #}*/
    /*{# non-step-band point (no partner) is unaffected: same result as #}*/
    /*{# localOriginIn. #}*/
    function bandAwareOrigin(point, container) {
        const at = localOriginIn(point, container);
        const partner = stepBandPartner(point);
        if (!at || !partner) return at;
        const partnerAt = localOriginIn(partner, container);
        if (!partnerAt) return at;
        return new DOMPoint((at.x + partnerAt.x) / 2, (at.y + partnerAt.y) / 2);
    }

    /*{# A mark is point-shaped only if Vega put it in a symbol group. This is #}*/
    /*{# what keeps a line's own path (one node for every datum) and an area's #}*/
    /*{# fill out of the marker path -- their coordinates would be meaningless. #}*/
    function isSymbolMark(el) {
        const group = el.closest(MARK_GROUP_SELECTOR);
        return !!group && group.classList.contains('mark-symbol');
    }

    /*{# What the proximity snap may land on: real data marks only. Every #}*/
    /*{# [aria-label] in a chart passes isDataMark, including a value label and #}*/
    /*{# a series-label rail's legend text -- snapping the cursor onto one of #}*/
    /*{# those would report a datum the cursor is nowhere near. #}*/
    function isSnappableMark(el) {
        return isSymbolMark(el) || isRecedableMark(el);
    }

    /*{# One overlay <g> per hover, appended last inside the chart's nested #}*/
    /*{# <svg> so it paints over the marks. Appending is the whole z-order #}*/
    /*{# story: no insertion-point search, and teardown is removing the node. #}*/
    function markerOverlay(chartEl, state) {
        const host = chartEl.querySelector('svg.marks');
        if (!host) return null;
        const overlay = svgNode('g', { class: 'dbt-hover-markers' });
        host.appendChild(overlay);
        state.emphasis.drawn.push(overlay);
        return overlay;
    }

    /*{# The datum's SERIES color, which is not always the color of the node #}*/
    /*{# the tooltip matched. A line is drawn as five stacked sublayers, and the #}*/
    /*{# first symbol layer in document order is a HALO painted in the background #}*/
    /*{# color -- so reading fill off the matched node yields the background and #}*/
    /*{# the marker vanishes into the card. Every sublayer of one datum carries #}*/
    /*{# the byte-identical aria-label, so the real color is found by scanning #}*/
    /*{# that datum's other symbol nodes for the one that is not the halo. #}*/
    /*{# With no ground rect there is no background to tell the halo apart #}*/
    /*{# from the real paint, so fall back to document order: Vega emits the #}*/
    /*{# halo first and the hover target last, so the LAST sublayer carries #}*/
    /*{# the series color. #}*/
    function datumColor(point, chartEl, background) {
        const label = point.getAttribute('aria-label');
        const all = chartEl.querySelectorAll('.mark-symbol [aria-label]');
        const mine = [];
        all.forEach(function (el) {
            if (el.getAttribute('aria-label') === label) mine.push(el);
        });
        if (background === null) {
            for (let i = mine.length - 1; i >= 0; i--) {
                const color = markSeriesColor(mine[i]);
                if (color) return color;
            }
        } else {
            for (let i = 0; i < mine.length; i++) {
                const color = markSeriesColor(mine[i]);
                if (color && color !== background) return color;
            }
        }
        return markSeriesColor(point);
    }

    /*{# The y-axes, merged into the vertical bands they occupy. A row facet #}*/
    /*{# gives each panel its own axis, so the bands come out disjoint and one #}*/
    /*{# band is one plot; a dual-axis chart has two axes over the same plot, #}*/
    /*{# which overlap and merge into one. So band COUNT answers "is this #}*/
    /*{# split into rows", and a band's foot is that plot's floor. #}*/
    function yAxisBands(chartEl) {
        const bands = [];
        chartEl.querySelectorAll('.role-axis[aria-label^="Y-axis"]').forEach(function (axis) {
            const r = axis.getBoundingClientRect();
            if (r.height <= 0) return;
            for (let i = 0; i < bands.length; i++) {
                const b = bands[i];
                const overlap = Math.min(b.bottom, r.bottom) - Math.max(b.top, r.top);
                if (overlap > 0.5 * Math.min(b.bottom - b.top, r.height)) {
                    b.top = Math.min(b.top, r.top);
                    b.bottom = Math.max(b.bottom, r.bottom);
                    return;
                }
            }
            bands.push({ top: r.top, bottom: r.bottom });
        });
        return bands;
    }

    function bandFootAt(bands, screenY) {
        for (let i = 0; i < bands.length; i++) {
            if (screenY >= bands[i].top && screenY <= bands[i].bottom) return bands[i].bottom;
        }
        return null;
    }

    function overlayYFromScreen(screenY, overlay) {
        if (screenY === null) return null;
        const m = overlay.getScreenCTM();
        return m ? new DOMPoint(0, screenY).matrixTransform(m.inverse()).y : null;
    }


    /*{# Which X-axis group belongs to the hovered mark's own panel. For a #}*/
    /*{# column facet, Vega-Lite draws the tick-labeled X-axis groups in a #}*/
    /*{# shared `role-column-footer` band -- a SIBLING of the plotting #}*/
    /*{# `.role-scope.cell`, never a descendant of any one panel -- one axis #}*/
    /*{# group per column, in the same left-to-right order as the cell's own #}*/
    /*{# per-panel children (verified against a live render). A single #}*/
    /*{# chart-wide querySelector therefore always finds panel 0's axis #}*/
    /*{# regardless of which panel is hovered -- it read correctly before only #}*/
    /*{# because every column panel happened to share the same vertical #}*/
    /*{# position, a coincidence of geometry, not a guarantee. panelScope() #}*/
    /*{# already resolves the hovered mark's own panel <g>; its ordinal #}*/
    /*{# position among the cell's children is the same ordinal position #}*/
    /*{# among the chart's X-axis groups. Degenerates to the (only) axis group #}*/
    /*{# for anything that isn't a multi-column facet -- a non-faceted chart, #}*/
    /*{# a chart with just one column, or a row facet (already routed through #}*/
    /*{# yAxisBands' `bands.length > 1` branch below before this is reached). #}*/
    function panelXAxis(mark, chartEl) {
        const axes = chartEl.querySelectorAll('.role-axis[aria-label^="X-axis"] > g');
        if (axes.length < 2) return axes[0] || null;
        const cell = mark.closest('.role-scope.cell');
        if (!cell) return axes[0];
        const panel = panelScope(mark, chartEl);
        const index = Array.prototype.indexOf.call(cell.children, panel);
        return index >= 0 && axes[index] ? axes[index] : axes[0];
    }

    /*{# Line/area: a dot on each datum the tooltip is describing, plus one #}*/
    /*{# drop line at that x from the topmost of them down to the axis. #}*/
    function drawDatumMarkers(overlay, chartEl, points, hovered) {
        const background = chartBackground(hovered);
        const spots = [];
        points.forEach(function (point) {
            const at = bandAwareOrigin(point, overlay);
            const color = datumColor(point, chartEl, background);
            if (at && color) spots.push({ x: at.x, y: at.y, color: color });
        });
        if (!spots.length) return;

        /*{# The drop line is anchored on the HOVERED datum's x -- every match #}*/
        /*{# shares it -- not on whichever series happens to sort first. Its #}*/
        /*{# color is a fixed theme neutral (DCT_HOVER_EMPHASIS.dropLineColor), #}*/
        /*{# never the hovered series' own paint: the line reads as "here is #}*/
        /*{# the x", the dots beside it already say which series is which. #}*/
        const anchor = bandAwareOrigin(hovered, overlay) || spots[0];

        /*{# Vega's grid group carries the same transform as the real x-axis #}*/
        /*{# but is aria-hidden, so the axis is identified by its label. A #}*/
        /*{# chart with the x-axis hidden has neither, and simply gets no #}*/
        /*{# drop line rather than a guessed baseline. #}*/
        /*{# Where the drop line stops. Three sources, in order of how directly #}*/
        /*{# each states the plot floor: #}*/
        /*{#                                                                  #}*/
        /*{# 1. Several y-axis bands means a row facet, and Vega gives one of #}*/
        /*{#    those a SINGLE x-axis at the foot of the whole chart -- the #}*/
        /*{#    right baseline only for the bottom panel, so the band holding #}*/
        /*{#    the datum wins over the axis here. #}*/
        /*{# 2. Otherwise the x-axis, which is the floor exactly -- scoped to #}*/
        /*{#    the hovered mark's own panel for a column facet (panelXAxis). #}*/
        /*{# 3. No x-axis at all (a board that hides it) used to end the drop #}*/
        /*{#    line's story. The y-axis spans the plot, so its own foot stands #}*/
        /*{#    in -- approximately: the axis rect includes its tick labels, #}*/
        /*{#    which overhang the floor by a pixel or two. Worth it to draw #}*/
        /*{#    the line at all, and only reached when nothing exact is left. #}*/
        const hoveredBox = hovered.getBoundingClientRect();
        const datumScreenY = hoveredBox.top + hoveredBox.height / 2;
        const bands = yAxisBands(chartEl);
        const axis = panelXAxis(hovered, chartEl);

        let baselineY = null;
        if (bands.length > 1) {
            baselineY = overlayYFromScreen(bandFootAt(bands, datumScreenY), overlay);
        } else if (axis) {
            const baseline = localOriginIn(axis, overlay);
            if (baseline) baselineY = baseline.y;
        } else if (bands.length === 1) {
            baselineY = overlayYFromScreen(bandFootAt(bands, datumScreenY), overlay);
        }
        if (baselineY !== null) {
            let top = spots[0].y;
            spots.forEach(function (spot) { if (spot.y < top) top = spot.y; });
            overlay.appendChild(svgNode('line', {
                x1: anchor.x, y1: top, x2: anchor.x, y2: baselineY,
                stroke: DCT_HOVER_EMPHASIS.dropLineColor,
                'stroke-width': DCT_HOVER_EMPHASIS.dropLineWidth,
            }));
        }

        spots.forEach(function (spot) {
            overlay.appendChild(svgNode('circle', {
                cx: spot.x, cy: spot.y, r: MARKER_DOT_RADIUS,
                fill: spot.color, stroke: background || 'none', 'stroke-width': MARKER_STROKE_WIDTH,
            }));
        });
    }

    /*{# Scatter: the point is already drawn, so emphasis is a ring around it. #}*/
    /*{# getBBox gives the rendered radius, which a size encoding varies per #}*/
    /*{# datum -- reading it beats assuming the theme's default. #}*/
    function drawPointRing(overlay, mark) {
        const at = localOriginIn(mark, overlay);
        if (!at) return;
        const box = mark.getBBox();
        const chartEl = mark.closest('.dbt-chart');
        overlay.appendChild(svgNode('circle', {
            cx: at.x, cy: at.y,
            r: (Math.max(box.width, box.height) / 2) + MARKER_RING_GAP,
            fill: 'none',
            stroke: datumColor(mark, chartEl, chartBackground(mark)),
            'stroke-width': MARKER_STROKE_WIDTH,
        }));
    }

    /*{# A column-faceted chart's mirrored right-edge y-axis (features/ #}*/
    /*{# mirror_axis.py) rides a ghost `opacity: 0` rule layer appended LAST #}*/
    /*{# per datum, purely to carry that axis -- it paints above the real #}*/
    /*{# symbol layers, so the mousemove handler resolves IT as `mark`, not #}*/
    /*{# the symbol underneath. Every sublayer of one datum carries the #}*/
    /*{# byte-identical aria-label (datumColor() above relies on exactly #}*/
    /*{# this), so the real symbol is found by re-scanning `mark`'s own #}*/
    /*{# PANEL (panelScope()) for the sibling that carries that label AND #}*/
    /*{# is shaped like a point. Returns `mark` itself when it's already a #}*/
    /*{# symbol -- the common case, and every non-faceted or row-faceted #}*/
    /*{# chart, where nothing sits above the symbols -- or null when no #}*/
    /*{# symbol sibling exists at all (a bar in a plain bar chart): callers #}*/
    /*{# must treat null as "nothing to mark here", not fall back to `mark`. #}*/
    function resolveSymbolMark(mark, chartEl) {
        if (isSymbolMark(mark)) return mark;
        const label = mark.getAttribute('aria-label');
        if (!label) return null;
        let found = null;
        panelScope(mark, chartEl).querySelectorAll('[aria-label]').forEach(function (el) {
            if (!found && isSymbolMark(el) && el.getAttribute('aria-label') === label) found = el;
        });
        return found;
    }

    /*{# `matches` is the tooltip's own row set, so the chart marks exactly #}*/
    /*{# what the bubble lists -- the two cannot disagree about what is being #}*/
    /*{# pointed at, the same contract applyHoverEmphasis keeps for bars. #}*/
    /*{# The hovered mark does not have to be the marked one. On a combo chart #}*/
    /*{# the x-unified tooltip already groups the bar and the overlay line at #}*/
    /*{# one x, so hovering either should mark the other's datum -- the bubble #}*/
    /*{# names both values either way, and a chart that answers one direction #}*/
    /*{# and not the other reads as a bug rather than as a rule. What decides #}*/
    /*{# whether there is anything to draw is the MATCH SET, not the cursor: #}*/
    /*{# a pure bar, pie or heatmap chart has no point marks in it, so the #}*/
    /*{# filter below empties and nothing is drawn, with no family test here. #}*/
    function applyHoverMarkers(state, mark, matches) {
        if (!DCT_HOVER_EMPHASIS.visible) return;
        const chartEl = mark.closest('.dbt-chart');
        if (!chartEl) return;

        const points = matches
            .map(function (m) { return resolveSymbolMark(m.mark, chartEl); })
            .filter(Boolean);
        if (!points.length) return;

        const overlay = markerOverlay(chartEl, state);
        if (!overlay) return;

        /*{# A connected series (line or area) reads its datum as a position ON #}*/
        /*{# something, so the marker restates that position: a dot, and a drop #}*/
        /*{# to the axis it is measured against. A scatter point is already a #}*/
        /*{# free position, so it only needs to be picked out. #}*/
        const hoveredPoint = resolveSymbolMark(mark, chartEl);
        if (chartEl.querySelector('.mark-line, .mark-area')) {
            /*{# Anchor on a point, never on the hovered mark when that is a bar: #}*/
            /*{# a bar path's own origin is its top-left corner, so anchoring #}*/
            /*{# there would put the drop line on the bar's edge and paint it the #}*/
            /*{# bar's color rather than the marked series'. Anchored on the #}*/
            /*{# point it lands on the band's center, which is the bar's center. #}*/
            drawDatumMarkers(overlay, chartEl, points, hoveredPoint || points[0]);
        } else if (hoveredPoint) {
            drawPointRing(overlay, hoveredPoint);
        }
    }

    /*{# Marks paint the series color as fill (bar/pie/point) or stroke (line); #}*/
    /*{# 'none' is Vega's explicit "not painted" value, not a real color. #}*/
    function markSeriesColor(mark) {
        const fill = mark.getAttribute('fill');
        if (fill && fill !== 'none') return fill;
        const stroke = mark.getAttribute('stroke');
        return stroke && stroke !== 'none' ? stroke : null;
    }

    /*{# A line is keyed with a line, not a filled square that reads as a bar. #}*/
    /*{# Its hover target is either the stroke itself or the invisible point a #}*/
    /*{# line layer lays over each datum to be hoverable (opacity 0). #}*/
    function isStrokedMark(mark) {
        const fill = mark.getAttribute('fill');
        if ((!fill || fill === 'none') && markSeriesColor(mark)) return true;
        return mark.getAttribute('aria-roledescription') === 'point' &&
            mark.getAttribute('opacity') === '0';
    }

    function seriesSwatch(color, asLine) {
        const sw = DCT_TOOLTIP_STYLE.swatch;
        const height = asLine ? 2 : sw.size;
        return '<span style="display:inline-block;width:' + sw.size + 'px;height:' + height +
            'px;border-radius:' + (asLine ? 1 : sw.radius) + 'px;' +
            'background:' + color + ';margin-right:6px;flex-shrink:0;"></span>';
    }

    /*{# active_marker: 'triangle' -- an edge-flush directional indicator on the #}*/
    /*{# hovered x-unified row, the alternative to the 'fill' background tint #}*/
    /*{# (stark's utilitarian look vs. every other theme's soft tint). Absolute- #}*/
    /*{# positioned inside the row's own left-padding gutter -- the row's left #}*/
    /*{# edge already sits at x = padding.left from the tooltip box's border, so #}*/
    /*{# left:-padding.left lands the wedge flush on the border line, pointing #}*/
    /*{# inward. Row content never shifts: this is an overlay, not a layout change. #}*/
    function triangleMarker(ts) {
        return '<span style="position:absolute;left:-' + ts.padding.left + 'px;top:50%;' +
            'transform:translateY(-50%);width:0;height:0;' +
            'border-top:5px solid transparent;border-bottom:5px solid transparent;' +
            'border-left:6px solid ' + ts.value.font.color + ';"></span>';
    }

    /*{# header/series rows: bold identity headline, swatch+bare-value series row, #}*/
    /*{# field label dropped on both (the LUT's role marker already tells us which #}*/
    /*{# is which -- no title-text matching needed). Both set the themed value #}*/
    /*{# color EXPLICITLY: without it the text inherits the page/iframe default #}*/
    /*{# (black), which vanishes on dark-box themes (stark, plain, editorial). #}*/
    function headerRow(entry, seriesColor, ts) {
        const swatch = entry.swatch && seriesColor ? seriesSwatch(seriesColor) : '';
        return '<div style="display:flex;align-items:center;font-weight:700;padding:2px 0 4px;color:' +
            ts.value.font.color + ';">' + swatch + formatValue(entry.value) + '</div>';
    }

    function seriesRow(entry, seriesColor, ts) {
        const color = entry.swatch || seriesColor;
        const swatch = color ? seriesSwatch(color) : '';
        return '<div style="display:flex;align-items:center;padding:2px 0;color:' +
            ts.value.font.color + ';">' + swatch + formatValue(entry.value) + '</div>';
    }

    /*{# dependent value row (today's label -> value shape); the footer total #}*/
    /*{# reuses it with a top border + heavier weight instead of a new layout. #}*/
    /*{# Splits a number-shaped display value around its decimal point so a #}*/
    /*{# column of them lines up on it: prefix + integer part, then the point, #}*/
    /*{# fraction and suffix. null when the value isn't number-shaped (a date). #}*/
    const DECIMAL_SHAPE = /^([^\d]*[\d,]*\d)(\.\d+)?([^\d]*)$/;
    function decimalParts(value) {
        const m = DECIMAL_SHAPE.exec(String(value));
        return m ? { whole: m[1], tail: (m[2] || '') + m[3] } : null;
    }

    /*{# The widest tail (point, fraction, suffix) among a tooltip's numbers, in #}*/
    /*{# ch: every tail cell takes this width, so the points share one x. #}*/
    function decimalTailWidth(entries) {
        let widest = 0;
        entries.forEach(function (e) {
            const parts = e.role === 'value' ? decimalParts(e.value) : null;
            if (parts) widest = Math.max(widest, parts.tail.length);
        });
        return widest;
    }

    function alignedValue(value, tailWidth) {
        const parts = tailWidth ? decimalParts(value) : null;
        if (!parts) return formatValue(value);
        return escapeHtml(parts.whole) +
            '<span style="display:inline-block;white-space:pre;text-align:left;min-width:' + tailWidth + 'ch;">' +
            escapeHtml(parts.tail) + '</span>';
    }

    function valueRow(entry, ts, isTotal, tailWidth) {
        const rowStyle = isTotal
            ? 'display:flex;justify-content:space-between;gap:' + ts.gap + 'px;margin-top:4px;padding-top:4px;border-top:' + DIVIDER_WIDTH + 'px solid ' + ts.border.color + ';'
            : 'display:flex;justify-content:space-between;gap:' + ts.gap + 'px;padding:2px 0;';
        const labelWeight = isTotal ? ts.value.font.weight : ts.label.font.weight;
        /*{# Contrast is color, not weight (matches the x-unified grid): a muted #}*/
        /*{# value drops to the low-contrast label color; weight stays uniform so #}*/
        /*{# the total's border still reads as the footer emphasis. #}*/
        const valueColor = entry.muted ? ts.label.font.color : ts.value.font.color;
        return (
            '<div style="' + rowStyle + '">' +
                '<span style="display:flex;align-items:center;color:' + ts.label.font.color + ';font-weight:' + labelWeight + ';">' + (entry.swatch ? seriesSwatch(entry.swatch) : '') + formatFieldName(entry.label) + '</span>' +
                '<span style="color:' + valueColor + ';font-weight:' + ts.value.font.weight + ';text-align:right;font-variant-numeric:tabular-nums lining-nums;">' + alignedValue(entry.value, tailWidth) + '</span>' +
            '</div>'
        );
    }

    /*{# The single-mark tooltip -- today's rendering, unchanged. This is the #}*/
    /*{# degenerate case of x-unified grouping (exactly one match) and the #}*/
    /*{# fallback when grouping doesn't apply (no header, misaligned-x lines) or #}*/
    /*{# cardinality exceeds XUNIFIED_ABANDON_THRESHOLD. #}*/
    /*{# An 'order' entry carries no `label` -- it's sort metadata for the #}*/
    /*{# x-unified path (xUnifiedRow filters to role === 'value' and never sees #}*/
    /*{# it), not a row to render here. Skipping it explicitly, rather than #}*/
    /*{# falling through to valueRow(), avoids formatFieldName(undefined). #}*/
    /*{# A value row with nothing to say (an optional second line, like a #}*/
    /*{# duration's other unit when it matches the first) is left out. #}*/
    function singleMarkHtml(entries, seriesColor, ts) {
        const tailWidth = decimalTailWidth(entries);
        return entries.map(function (entry) {
            if (entry.role === 'header') return headerRow(entry, seriesColor, ts);
            if (entry.role === 'series') return seriesRow(entry, seriesColor, ts);
            if (entry.role === 'order') return '';
            if (entry.role === 'value' && entry.value === '') return '';
            return valueRow(entry, ts, entry.role === 'total', tailWidth);
        }).join('');
    }

    /*{# One cell inside the x-unified grid body. Rounding is applied ONLY to #}*/
    /*{# the first/last cell of a logical row: with the grid's column-gap:0, #}*/
    /*{# adjoining cells' backgrounds touch, so rounding just the two end #}*/
    /*{# corners makes a hovered row's per-cell tint read as ONE continuous #}*/
    /*{# span instead of a series of separate boxes with visible gaps. #}*/
    /*{# Spacing between columns comes from padding-right (never the gap), per #}*/
    /*{# the locked design -- the last cell in a row gets none. #}*/
    function xUnifiedCell(html, style, activeBg, isFirst, isLast) {
        let radius = '';
        if (isFirst) radius += 'border-top-left-radius:3px;border-bottom-left-radius:3px;';
        if (isLast) radius += 'border-top-right-radius:3px;border-bottom-right-radius:3px;';
        /*{# Outer cells carry horizontal breathing room so the hovered-row tint #}*/
        /*{# insets a little past the swatch and past the last value, rather than #}*/
        /*{# sitting flush against the content edges (the "too tight" regression). #}*/
        const paddingLeft = isFirst ? '6px' : '0';
        const paddingRight = isLast ? '6px' : '8px';
        return '<div style="padding:2px ' + paddingRight + ' 2px ' + paddingLeft + ';' + (activeBg || '') + radius + style + '">' + html + '</div>';
    }

    /*{# One row in the x-unified bubble: swatch, series name, percent (value/ #}*/
    /*{# foreground color, only when the bubble's rows carry one), and raw #}*/
    /*{# value (label/dim color, weight 500, no parens) -- each its own grid #}*/
    /*{# cell so percent and raw form true right-aligned columns regardless of #}*/
    /*{# digit count, instead of one drifting joined string. #}*/
    function xUnifiedRow(entries, seriesColor, isActive, ts, hasPercent, asLine, recede) {
        const series = markSeriesEntry(entries);
        const values = entries.filter(function (e) { return e.role === 'value'; });
        const label = series ? formatValue(series.value) : (values[0] ? formatFieldName(values[0].label) : '');
        const swatch = seriesColor ? seriesSwatch(seriesColor, asLine) : '';

        /*{# active_marker='fill' (default): background-only tint on the row that #}*/
        /*{# triggered the hover -- no border/weight change, reads as a subtle tint, #}*/
        /*{# not a redraw. active_marker='triangle' (stark): an edge-flush wedge #}*/
        /*{# anchored to the row's first (swatch) cell instead -- see triangleMarker(). #}*/
        const useTriangle = ts.activeMarker === 'triangle';
        const activeBg = (isActive && !useTriangle) ? 'background:rgba(127,127,127,0.16);' : '';
        const marker = (isActive && useTriangle) ? triangleMarker(ts) : '';

        const swatchCell = xUnifiedCell(marker + swatch, 'position:relative;', activeBg, true, false);
        const nameCell = xUnifiedCell(label, 'color:' + ts.label.font.color + ';', activeBg, false, false);

        /*{# Weight is uniform across every numeric cell (matching the total row); #}*/
        /*{# lead-vs-companion contrast is carried by COLOR, not weight. #}*/
        const valueWeight = 'font-weight:' + ts.value.font.weight + ';';

        let pctCell = '';
        let valueText;
        if (hasPercent) {
            pctCell = xUnifiedCell(
                formatValue(values[0].value),
                'text-align:right;color:' + ts.value.font.color + ';' + valueWeight + 'font-variant-numeric:tabular-nums lining-nums;',
                activeBg, false, false
            );
            valueText = values.length > 1 ? formatValue(values[values.length - 1].value) : '';
        } else {
            valueText = values[0] ? formatValue(values[0].value) : '';
        }
        /*{# Contrast rule: the lead value takes the high-contrast value color; a #}*/
        /*{# value drops to the low-contrast label color ONLY when it's the raw #}*/
        /*{# companion beside a percent (there the % is the lead). A sole value #}*/
        /*{# (no % column) IS its row's lead -> value color, matching single-mark. #}*/
        const valueColor = (hasPercent || recede) ? ts.label.font.color : ts.value.font.color;
        const valueCell = xUnifiedCell(
            valueText,
            'text-align:right;color:' + valueColor + ';' + valueWeight + 'font-variant-numeric:tabular-nums lining-nums;',
            activeBg, false, true
        );

        return swatchCell + nameCell + pctCell + valueCell;
    }

    /*{# "+N more" overflow row for the series folded out of view. It carries NO #}*/
    /*{# value: the hidden rows are never summed here. Summing them client-side #}*/
    /*{# would fabricate a total for mixed-unit/combo families the emit layer never #}*/
    /*{# gated a total for -- the same no-fabrication rule the footer total follows #}*/
    /*{# via findTotalEntry -- and would be lossy regardless, since each row's value #}*/
    /*{# is an already-formatted display string (grouping separators and all). #}*/
    /*{# When a real group total exists it is shown by the footer total row below. #}*/
    /*{# Fits the same grid as the data rows: name-column "+N more", value blank. #}*/
    function xUnifiedRemainderRow(remainder, ts, hasPercent) {
        const swatchCell = xUnifiedCell('', '', '', true, false);
        const nameCell = xUnifiedCell('+' + remainder.length + ' more', 'font-style:italic;color:' + ts.label.font.color + ';', '', false, false);
        const pctCell = hasPercent ? xUnifiedCell('', '', '', false, false) : '';
        const valueCell = xUnifiedCell('', '', '', false, true);
        return swatchCell + nameCell + pctCell + valueCell;
    }

    /*{# The shared identity header, spanning every grid column with a bottom #}*/
    /*{# border separating it from the rows below -- same header content as #}*/
    /*{# the single-mark path (headerRow, untouched), just wrapped to span. #}*/
    function xUnifiedHeaderCell(headerEntriesList, ts) {
        const inner = headerEntriesList.map(function (h) { return headerRow(h, null, ts); }).join('');
        return '<div style="grid-column:1/-1;border-bottom:' + DIVIDER_WIDTH + 'px solid ' + ts.border.color + ';padding-bottom:4px;margin-bottom:4px;">' + inner + '</div>';
    }

    /*{# Footer total: the label spans grid-column 1 through the percent #}*/
    /*{# column (or through name when there's no percent column), and the #}*/
    /*{# value cell sits in the value column -- both carry border-top, and with #}*/
    /*{# the grid's column-gap:0 the two segments read as ONE unbroken line. #}*/
    function xUnifiedTotalRow(entry, ts, hasPercent) {
        const borderTop = 'border-top:' + DIVIDER_WIDTH + 'px solid ' + ts.border.color + ';';
        const labelSpan = hasPercent ? '1 / span 3' : '1 / span 2';
        const label = (
            '<div style="grid-column:' + labelSpan + ';' + borderTop +
                'margin-top:4px;padding:4px 8px 0 0;color:' + ts.label.font.color + ';font-weight:' + ts.value.font.weight + ';">' +
                formatFieldName(entry.label) +
            '</div>'
        );
        /*{# The total tracks the weight of the numbers it sums: dim when a #}*/
        /*{# percent leads the bubble (the total is a raw sum, matching its dim raw #}*/
        /*{# rows -- normalized stack, and pie via its own muted marker), dark when #}*/
        /*{# there's no percent (the value is itself the lead -- absolute stack). #}*/
        const totalColor = hasPercent ? ts.label.font.color : ts.value.font.color;
        const value = (
            '<div style="' + borderTop + 'margin-top:4px;padding:4px 6px 0 0;text-align:right;color:' +
                totalColor + ';font-weight:' + ts.value.font.weight + ';font-variant-numeric:tabular-nums lining-nums;">' +
                formatValue(entry.value) +
            '</div>'
        );
        return label + value;
    }

    /*{# True when this row carries its own ROLE_TOTAL entry -- the shared #}*/
    /*{# group-total transform stamps one onto every commensurable base segment; #}*/
    /*{# a combo overlay reference never does (_layer_tooltip_description's "no #}*/
    /*{# total" contract in emitters/_overlay.py). Only meaningful as a base-part #}*/
    /*{# vs. overlay-reference split when the bubble actually HAS a total (see #}*/
    /*{# the caller below) -- a bubble with no total at all (a non-combo grouped #}*/
    /*{# bar/multi-series line, or a single-series combo base + target) carries #}*/
    /*{# no ROLE_TOTAL on any row, so this predicate can't distinguish base from #}*/
    /*{# overlay there; there's nothing to sandwich a footer between anyway. #}*/
    function carriesGroupTotal(match) {
        return match.entries.some(function (e) { return e.role === 'total'; });
    }

    /*{# The x-unified bubble: a zero-column-gap CSS grid (swatch/name/%/value), #}*/
    /*{# shared header spanning it once, then rows -- the footer total ONLY when #}*/
    /*{# a matched mark actually carries a ROLE_TOTAL entry (the emit layer #}*/
    /*{# already gates that to commensurable/additive families: normalized #}*/
    /*{# stacks, pie; this never computes a total itself for a family that has #}*/
    /*{# none). When a total exists, base-part rows (the ones carrying it) come #}*/
    /*{# first, then the "+N more" remainder, then the Total footer, then any #}*/
    /*{# combo overlay/reference rows (e.g. a target line) LAST -- the Total is #}*/
    /*{# the buildup the overlay is compared against, so it must land between #}*/
    /*{# the parts and the overlay, never after it. When there's no total at all #}*/
    /*{# (plain multi-series charts, or a combo whose base has none), every row #}*/
    /*{# renders together in its baked/legend order with the remainder last -- #}*/
    /*{# unchanged, prior behavior; the base/overlay split only matters when #}*/
    /*{# there's a footer to position between the two groups. #}*/
    /*{# The percent column is decided once per bubble (hasPercent) from #}*/
    /*{# whether any matched row carries 2 values ([%, raw], vs. 1 for a plain #}*/
    /*{# single value) and collapses to 3 columns when absent. #}*/
    function xUnifiedHtml(hoveredMark, hoveredEntries, matches, ts) {
        const plan = buildRowPlan(matches, hoveredMark);
        const hasPercent = matches.some(function (m) {
            return m.entries.filter(function (e) { return e.role === 'value'; }).length > 1;
        });
        const columns = hasPercent ? 'auto minmax(0,1fr) auto auto' : 'auto minmax(0,1fr) auto';
        /*{# When the legend puts an overlay ahead of the stacked parts (a bullet: #}*/
        /*{# actual and goal, then the bands they are read against), the parts are #}*/
        /*{# a backdrop: no total sums them, and only the leading row keeps the #}*/
        /*{# loud value color -- everything after it is reference. #}*/
        const overlayLeads = plan.shown.length > 0 && !carriesGroupTotal(plan.shown[0]) &&
            plan.shown.some(carriesGroupTotal);
        const totalEntry = overlayLeads ? null : findTotalEntry(matches);
        function renderRow(m, i) {
            return xUnifiedRow(
                m.entries, markSeriesColor(m.mark), m.mark === hoveredMark, ts, hasPercent,
                isStrokedMark(m.mark), overlayLeads && i > 0
            );
        }

        let html = '<div style="display:grid;grid-template-columns:' + columns + ';column-gap:0;align-items:center;">';
        html += xUnifiedHeaderCell(headerEntries(hoveredEntries), ts);
        if (totalEntry) {
            const baseShown = plan.shown.filter(carriesGroupTotal);
            const overlayShown = plan.shown.filter(function (m) { return !carriesGroupTotal(m); });
            html += baseShown.map(renderRow).join('');
            if (plan.remainder.length) {
                html += xUnifiedRemainderRow(plan.remainder, ts, hasPercent);
            }
            html += xUnifiedTotalRow(totalEntry, ts, hasPercent);
            // Detach the overlay reference row(s) (combo target/goal) from the
            // parts+total group: the sum line above the total already binds the
            // total to the parts it sums, so a target sitting flush below reads
            // as "grouped with the total". A full-width gap re-frames it as a
            // separate comparison against that total, not a member of the stack.
            if (overlayShown.length) {
                html += '<div style="grid-column:1/-1;height:' + OVERLAY_DETACH_GAP + 'px;"></div>';
            }
            html += overlayShown.map(renderRow).join('');
        } else {
            html += plan.shown.map(renderRow).join('');
            if (plan.remainder.length) {
                html += xUnifiedRemainderRow(plan.remainder, ts, hasPercent);
            }
        }
        html += '</div>';
        return html;
    }

    function showTooltip(state, mark, event, svg) {
        const entries = parseAriaLabel(mark.getAttribute('aria-label'));
        if (!entries.length) {
            hideTooltip(state);
            return;
        }

        const ts = DCT_TOOLTIP_STYLE;
        state.activeMark = mark;
        const matches = svg ? collectMatchingMarks(svg, mark, entries) : [{ mark: mark, entries: entries }];
        const useXUnified = matches.length > 1 && matches.length <= XUNIFIED_ABANDON_THRESHOLD;

        state.tooltip.innerHTML = useXUnified
            ? xUnifiedHtml(mark, entries, matches, ts)
            : singleMarkHtml(entries, markSeriesColor(mark), ts);
        state.tooltip.style.display = 'block';
        positionTooltip(state, event.clientX, event.clientY);

        /*{# Emphasize exactly the rows the tooltip is describing -- the whole #}*/
        /*{# match set when the bubble grouped them, otherwise the single #}*/
        /*{# hovered datum, never the full (unshown) set. One source of truth, #}*/
        /*{# so the bubble and the chart can never disagree about what is #}*/
        /*{# being pointed at. #}*/
        const kept = {};
        if (useXUnified) {
            matches.forEach(function (m) { kept[m.mark.getAttribute('aria-label')] = true; });
        } else {
            /*{# The node under the cursor is often an invisible hover band #}*/
            /*{# painted above its own bar, but it carries that bar's label #}*/
            /*{# verbatim -- so keying on the label needs no unwrapping. #}*/
            kept[mark.getAttribute('aria-label')] = true;
        }
        applyHoverEmphasis(state, mark, kept);
        applyHoverMarkers(state, mark, useXUnified ? matches : [{ mark: mark }]);
    }

    /*{# Bound once, at the board root: each chart is a nested standalone <svg> #}*/
    /*{# whose events bubble up to it. #}*/
    function initializeSvg(svg) {
        if (!svg.matches('[data-dbt-tooltip-style]') || svg.dataset.dbtHoverInitialized === 'true') {
            return;
        }

        svg.dataset.dbtHoverInitialized = 'true';
        readBoardConfig(svg);
        const state = ensureState();

        svg.addEventListener('mousemove', function (event) {
            const target = event.target;
            const labeledTarget = target && target.closest
                ? event.target.closest('[aria-label]')
                : null;
            let mark = labeledTarget && isDataMark(labeledTarget)
                ? labeledTarget
                : null;

            /*{# Vega area paths are aria-hidden because one path represents every #}*/
            /*{# datum; their labeled point siblings carry the correct values. #}*/
            /*{# Layered marks can have the same visible-mark/labeled-sibling split. #}*/
            if (!mark && target && target.closest && target.closest(MARK_GROUP_SELECTOR)) {
                const chart = target.closest('.dbt-chart');
                if (chart) {
                    mark = nearestDataMark(chart, event.clientX, event.clientY);
                }
            }

            /*{# Cursor inside the chart but on no mark at all -- the gap between #}*/
            /*{# scatter points, the gap between two bars, or beside a line whose #}*/
            /*{# datum target the page scale has shrunk below a comfortable #}*/
            /*{# target. Without this a drag along a bar chart drops the tooltip #}*/
            /*{# in every gap and picks it back up on the next bar, which reads #}*/
            /*{# as flicker rather than as tracking. Bounded, so a mark still #}*/
            /*{# cannot claim the cursor from across the plot. The unbounded branch #}*/
            /*{# above keeps its own behavior: there the cursor IS on a datum #}*/
            /*{# (an area fill) and is only looking for that datum's labeled #}*/
            /*{# sibling, which can legitimately sit far away. #}*/
            /*{#                                                                  #}*/
            /*{# Chart furniture (a legend, a title) never holds a snappable #}*/
            /*{# mark, so skip the sweep entirely rather than pay for it -- a #}*/
            /*{# cheap exit, not a substitute for the panel/radius scoping below. #}*/
            if (!mark && target && target.closest
                && !target.closest('.role-legend') && !target.closest('.role-title')) {
                const chart = target.closest('.dbt-chart');
                if (chart) {
                    /*{# Scoped to the cursor's own panel -- see panelAtPoint(). A #}*/
                    /*{# small-multiples facet packs every panel's marks under one #}*/
                    /*{# shared `.role-scope.cell`, so an unscoped search here would #}*/
                    /*{# snap the cursor into a neighboring panel's mark whenever it #}*/
                    /*{# is within radius, even from the gutter between panels. #}*/
                    const panel = panelAtPoint(chart, event.clientX, event.clientY);
                    if (panel) {
                        mark = nearestDataMark(
                            panel, event.clientX, event.clientY, HOVER_SNAP_RADIUS_PX, isSnappableMark
                        );
                    }
                }
            }

            if (!mark || !svg.contains(mark)) {
                if (state.activeMark) {
                    hideTooltip(state);
                }
                return;
            }

            if (state.activeMark !== mark || state.tooltip.style.display === 'none') {
                showTooltip(state, mark, event, svg);
            } else {
                positionTooltip(state, event.clientX, event.clientY);
            }
        });

        svg.addEventListener('mouseleave', function () {
            hideTooltip(state);
        });
    }

    /*{# Hosts that swap boards in place hand the new one here (the controls #}*/
    /*{# runtime's mount() does); a page that loads once binds on ready. #}*/
    window.dbtChartHover = {
        mount: function (root) {
            const scope = root || document;
            if (scope.matches && scope.matches('svg')) initializeSvg(scope);
            scope.querySelectorAll('svg').forEach(initializeSvg);
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { window.dbtChartHover.mount(); }, { once: true });
    } else {
        window.dbtChartHover.mount();
    }
})();
