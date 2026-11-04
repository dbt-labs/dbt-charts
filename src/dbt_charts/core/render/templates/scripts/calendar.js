/*{# The month calendar the date controls (variables.js) and a table's date #}*/
/*{# filter (table_runtime.js) both draw, so a range is picked the same way #}*/
/*{# everywhere. Days are ISO `YYYY-MM-DD` at the edges and local-midnight #}*/
/*{# Dates inside. The calendar area carries `data-dbt-time-locale` (month and #}*/
/*{# day names, first day of the week) from the board's time locale. #}*/
(function() {
    /*{# Fills a word's `{name}` placeholders with the date's parts. #}*/
    function fill(template, values) {
        return template.replace(/\{(\w+)\}/g, function(_, name) { return values[name]; });
    }

    /*{# Local midnight for today. #}*/
    function startOfToday() {
        var now = new Date();
        return new Date(now.getFullYear(), now.getMonth(), now.getDate());
    }

    /*{# ISO YYYY-MM-DD from a Date (local time). #}*/
    function toISO(d) {
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return d.getFullYear() + '-' + m + '-' + day;
    }

    var ISO_RE = /^\d{4}-\d{2}-\d{2}$/;

    /*{# Date from an ISO string (local midnight), null if malformed or rolled. #}*/
    function fromISO(s) {
        if (!ISO_RE.test(s)) return null;
        var parts = s.split('-');
        var d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
        if (d.getFullYear() !== parseInt(parts[0], 10) ||
            d.getMonth()    !== parseInt(parts[1], 10) - 1 ||
            d.getDate()     !== parseInt(parts[2], 10)) return null;
        return d;
    }

    function monthOf(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }

    /*{# Header (prev/month-name/next), DOW row, and a bare 42-cell month grid #}*/
    /*{# (today/other-month marked, one click handler per cell, an optional #}*/
    /*{# hover handler). Callers paint selection state themselves afterward. #}*/
    /*{# Month nav hands the new month back to `onNav` rather than redrawing: #}*/
    /*{# the caller's footer and painted selection sit in `calEl` too, and only #}*/
    /*{# the caller's own rebuild redraws them. #}*/
    function grid(calEl, words, viewMonth, onPickDay, onNav, onHoverDay) {
        var names     = JSON.parse(calEl.getAttribute('data-dbt-time-locale'));
        var year      = viewMonth.getFullYear();
        var month     = viewMonth.getMonth();
        var firstDay  = new Date(year, month, 1);
        var startWeek = (firstDay.getDay() - names.weekStart + 7) % 7;
        var monthName = fill(words.calendar_header, { month: names.months[month], year: year });
        /*{# Per rebuild, not per bind: a calendar opened after midnight in a #}*/
        /*{# long-lived tab must bold the actual today. #}*/
        var today = startOfToday();

        calEl.innerHTML = '';

        var header = document.createElement('div');
        header.className = 'dbt-cal-header';

        var prevBtn = document.createElement('button');
        prevBtn.type = 'button';
        prevBtn.className = 'dbt-cal-nav';
        prevBtn.setAttribute('aria-label', words.previous_month);
        prevBtn.textContent = '‹';
        prevBtn.addEventListener('click', function() {
            onNav(new Date(year, month - 1, 1));
        });

        var monthSpan = document.createElement('span');
        monthSpan.className = 'dbt-cal-month';
        monthSpan.textContent = monthName;

        var nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.className = 'dbt-cal-nav';
        nextBtn.setAttribute('aria-label', words.next_month);
        nextBtn.textContent = '›';
        nextBtn.addEventListener('click', function() {
            onNav(new Date(year, month + 1, 1));
        });

        header.appendChild(prevBtn);
        header.appendChild(monthSpan);
        header.appendChild(nextBtn);
        calEl.appendChild(header);

        var cells = document.createElement('div');
        cells.className = 'dbt-cal-grid';

        names.shortDays.slice(names.weekStart).concat(names.shortDays.slice(0, names.weekStart)).forEach(function(d) {
            var dow = document.createElement('div');
            dow.className = 'dbt-cal-dow';
            dow.textContent = d;
            cells.appendChild(dow);
        });

        var cursor = new Date(year, month, 1 - startWeek);
        for (var i = 0; i < 42; i++) {
            var dt      = new Date(cursor);
            var isOther = dt.getMonth() !== month;
            var isToday = dt.getTime() === today.getTime();
            var cell    = document.createElement('button');
            cell.type      = 'button';
            cell.className = 'dbt-cal-cell';
            if (isOther) { cell.classList.add('dbt-other-month'); cell.tabIndex = -1; }
            if (isToday) cell.classList.add('dbt-today');
            cell.textContent = dt.getDate();
            cell.dataset.day = String(dt.getTime());
            cell.setAttribute('aria-label', fill(words.calendar_day, {
                weekday: names.days[dt.getDay()],
                day: dt.getDate(),
                month: names.months[dt.getMonth()],
                year: dt.getFullYear(),
            }));
            (function(cellEl, cellDate) {
                cellEl.addEventListener('click', function() { onPickDay(cellDate); });
                if (onHoverDay) {
                    cellEl.addEventListener('mouseenter', function() { onHoverDay(cellDate); });
                }
            }(cell, dt));
            cells.appendChild(cell);
            cursor.setDate(cursor.getDate() + 1);
        }
        calEl.appendChild(cells);
    }

    /*{# A two-click range on `calEl`. `onPick(range)` hears every pick, with #}*/
    /*{# one end null until the second; `footer(range)` returns the element #}*/
    /*{# drawn under the grid on each rebuild, or null; `afterBuild()`, when #}*/
    /*{# given, runs after each one. `show(range, month)` seeds a [start, end] #}*/
    /*{# pair of Dates or nulls and opens on its later end, else on `month`, #}*/
    /*{# else where the view already was. #}*/
    function rangePicker(calEl, words, hooks) {
        var range     = [null, null];
        var hoverDate = null;
        var viewMonth = new Date();

        function rebuild() {
            grid(calEl, words, viewMonth,
                function(dt) { pick(dt); },
                function(newMonth) { viewMonth = newMonth; rebuild(); },
                function(dt) {
                    if (Boolean(range[0]) !== Boolean(range[1])) {
                        hoverDate = new Date(dt.getTime());
                        paint();
                    }
                }
            );
            var foot = hooks.footer(range);
            if (foot) calEl.appendChild(foot);
            paint();
            if (hooks.afterBuild) hooks.afterBuild();
        }

        function paint() {
            var startTs    = range[0] && range[0].getTime();
            var endTs      = range[1] && range[1].getTime();
            var anchorTs   = startTs || endTs;
            var previewing = Boolean(startTs) !== Boolean(endTs) && hoverDate;
            var hoverTs    = previewing && hoverDate.getTime();
            var lo         = previewing ? Math.min(anchorTs, hoverTs) : null;
            var hi         = previewing ? Math.max(anchorTs, hoverTs) : null;

            calEl.querySelectorAll('.dbt-cal-cell').forEach(function(c) {
                var ts           = parseInt(c.dataset.day, 10);
                var isStart      = startTs && ts === startTs;
                var isEnd        = endTs   && ts === endTs;
                var inRange      = startTs && endTs && ts > startTs && ts < endTs;
                var inPreview    = previewing && ts > lo && ts < hi;
                var isPreviewEnd = previewing && ts === hoverTs && ts !== anchorTs;

                c.classList.toggle('dbt-selected',         Boolean(isStart || isEnd));
                c.classList.toggle('dbt-range-start',      Boolean(isStart));
                c.classList.toggle('dbt-range-end',        Boolean(isEnd));
                c.classList.toggle('dbt-in-range',         Boolean(inRange));
                c.classList.toggle('dbt-preview-in-range', Boolean(inPreview));
                c.classList.toggle('dbt-preview-end',      Boolean(isPreviewEnd));
            });
        }

        function pick(dt) {
            if (dt.getMonth() !== viewMonth.getMonth()) viewMonth = monthOf(dt);
            if (!range[0] && range[1]) {
                /*{# An until-only value: a pick past the end makes the old end the start. #}*/
                range = dt > range[1] ? [range[1], dt] : [dt, range[1]];
            } else if (!range[0] || range[1]) {
                /*{# First pick (or re-start after a completed pair). #}*/
                range = [dt, null];
            } else if (dt < range[0]) {
                /*{# Second pick before the first — normalize to [earlier, later]. #}*/
                range = [dt, range[0]];
            } else if (dt.getTime() === range[0].getTime()) {
                range = [dt, dt];
            } else {
                range = [range[0], dt];
            }
            hoverDate = null;
            hooks.onPick(range);
            rebuild();
        }

        calEl.addEventListener('mouseleave', function() {
            if (hoverDate) { hoverDate = null; paint(); }
        });

        return {
            show: function(newRange, month) {
                range = newRange;
                hoverDate = null;
                var shown = range[1] || range[0] || month;
                if (shown) viewMonth = monthOf(shown);
                rebuild();
            },
        };
    }

    window.dbtCalendar = {
        fill: fill,
        startOfToday: startOfToday,
        toISO: toISO,
        fromISO: fromISO,
        grid: grid,
        rangePicker: rangePicker,
    };
})();
