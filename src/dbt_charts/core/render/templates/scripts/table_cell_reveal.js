/*{# The full value of a table cell the engine cut to its column. The engine
   publishes the whole text on the cut cell as `data-dbt-full`. Hovering the
   cell shows it in a card anchored under the cell; the pointer can move onto
   the card, which then offers "Open", and a click pins it in place as a
   popover whose text can be selected and copied. Escape, a click outside or
   its close button dismisses it. The card is page chrome, outside the board,
   so no click on a cell changes meaning. Bound once on the document, so it
   serves every board on the page, a re-rendered one included. #}*/
(function() {
    if (window.dbtTableCellReveal) return;

    /*{# How long the card waits for the pointer to cross from the cell to it. #}*/
    var LEAVE_GRACE_MS = 200;
    var GAP = 4;

    var card = null;
    var text = null;
    var cardCell = null;
    var pinned = false;
    var hideTimer = null;

    /*{# A cell in a linked row is inert to the pointer so a click reaches the #}*/
    /*{# row band; the band sits in the same row group, so the cell under the #}*/
    /*{# pointer is found by its box. #}*/
    function cellAt(event) {
        var target = event.target;
        if (!(target instanceof Element)) return null;
        var direct = target.closest('text[data-dbt-full]');
        if (direct) return direct;
        var row = target.closest('.dbt-table-row');
        if (!row) return null;
        var cells = row.querySelectorAll('text[data-dbt-full]');
        for (var i = 0; i < cells.length; i++) {
            var box = cells[i].getBoundingClientRect();
            if (event.clientX >= box.left && event.clientX <= box.right
                && event.clientY >= box.top && event.clientY <= box.bottom) {
                return cells[i];
            }
        }
        return null;
    }

    function ensureCard() {
        if (card) return card;
        card = document.createElement('div');
        card.className = 'dbt-table-cell-tip';
        card.setAttribute('role', 'tooltip');
        text = document.createElement('div');
        text.className = 'dbt-table-cell-tip-text';
        var open = document.createElement('span');
        open.className = 'dbt-table-cell-tip-open';
        open.setAttribute('aria-hidden', 'true');
        open.textContent = 'Open';
        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'dbt-table-cell-tip-close';
        close.setAttribute('aria-label', 'Close');
        close.textContent = '×';
        close.addEventListener('click', function(event) {
            event.stopPropagation();
            hide();
        });
        card.appendChild(text);
        card.appendChild(open);
        card.appendChild(close);
        card.addEventListener('mouseenter', function() {
            clearTimeout(hideTimer);
            if (pinned) return;
            /*{# Restart the hint's fade on every entry. #}*/
            card.classList.remove('dbt-table-cell-tip-hot');
            void card.offsetWidth;
            card.classList.add('dbt-table-cell-tip-hot');
        });
        card.addEventListener('mouseleave', function() {
            if (!pinned) scheduleHide();
        });
        card.addEventListener('click', function() {
            if (!pinned) pin();
        });
        document.body.appendChild(card);
        return card;
    }

    /*{# The card takes the theme of the board it reveals, read off the board #}*/
    /*{# root the hover runtime reads too (data-dbt-tooltip-style), so it #}*/
    /*{# matches the board's chart tooltips; boards on one page may differ. #}*/
    function applyTheme(el, cell) {
        var root = cell.closest('[data-dbt-tooltip-style]');
        if (!root) throw new Error('table cell reveal: cut cell outside a board root');
        var ts = JSON.parse(root.getAttribute('data-dbt-tooltip-style'));
        var pad = ts.padding;
        var vars = {
            '--dbt-tip-bg': ts.background,
            '--dbt-tip-fg': ts.value.font.color,
            '--dbt-tip-font': root.getAttribute('data-dbt-font-family'),
            '--dbt-tip-size': ts.font.size + 'px',
            '--dbt-tip-line': String(ts.lineHeight),
            '--dbt-tip-pad': pad.top + 'px ' + pad.right + 'px ' + pad.bottom + 'px ' + pad.left + 'px',
            '--dbt-tip-border': ts.border.width + 'px solid ' + ts.border.color,
            '--dbt-tip-radius': ts.border.radius + 'px',
            '--dbt-tip-shadow': ts.shadow.visible ? '0 10px 25px rgba(15, 23, 42, 0.25)' : 'none',
        };
        for (var name in vars) el.style.setProperty(name, vars[name]);
    }

    function show(cell) {
        var el = ensureCard();
        applyTheme(el, cell);
        cardCell = cell;
        text.textContent = cell.getAttribute('data-dbt-full');
        el.classList.remove('dbt-table-cell-tip-hot');
        el.style.display = 'block';
        el.style.left = '0px';
        el.style.top = '0px';
        var anchor = cell.getBoundingClientRect();
        var box = el.getBoundingClientRect();
        var left = Math.min(anchor.left, window.innerWidth - box.width - GAP);
        var top = anchor.bottom + GAP;
        if (top + box.height > window.innerHeight) top = anchor.top - GAP - box.height;
        el.style.left = Math.max(GAP, left) + 'px';
        el.style.top = Math.max(GAP, top) + 'px';
    }

    function pin() {
        pinned = true;
        card.classList.remove('dbt-table-cell-tip-hot');
        card.classList.add('dbt-table-cell-tip-pinned');
        card.setAttribute('role', 'dialog');
    }

    function hide() {
        clearTimeout(hideTimer);
        if (!card) return;
        pinned = false;
        cardCell = null;
        card.style.display = 'none';
        card.classList.remove('dbt-table-cell-tip-pinned', 'dbt-table-cell-tip-hot');
        card.setAttribute('role', 'tooltip');
    }

    function scheduleHide() {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(hide, LEAVE_GRACE_MS);
    }

    document.addEventListener('mousemove', function(event) {
        if (pinned || (card && card.contains(event.target))) return;
        var cell = cellAt(event);
        if (cell) {
            clearTimeout(hideTimer);
            if (cell !== cardCell) show(cell);
        } else if (cardCell) {
            scheduleHide();
        }
    });
    document.addEventListener('mousedown', function(event) {
        if (pinned && !card.contains(event.target)) hide();
    }, true);
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && cardCell) hide();
    });
    /*{# The card is fixed to the viewport, so it leaves with its cell on a #}*/
    /*{# scroll; scrolling inside the card itself keeps it. #}*/
    document.addEventListener('scroll', function(event) {
        if (cardCell && !(event.target instanceof Node && card.contains(event.target))) hide();
    }, true);

    window.dbtTableCellReveal = { show: show, pin: pin, hide: hide };
})();
