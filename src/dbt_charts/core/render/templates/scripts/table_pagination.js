/*{# Static-export table pagination. Every page's rows and paginator are drawn
   into the SVG already (the engine's table family); this only toggles which
   `data-dbt-table-page` group is visible. No re-render, no server round trip
   -- the same reason the chart hover runtime (chart_interactivity.js) ships
   inline instead of depending on a host: the artifact must work standalone. #}*/
(function () {
    function initializeSvg(svg) {
        svg.addEventListener('click', function (event) {
            var hit = event.target.closest('[data-dbt-page-target]');
            if (!hit) {
                return;
            }
            var pageGroup = hit.closest('[data-dbt-table-page]');
            if (!pageGroup) {
                return;
            }
            var chartId = pageGroup.getAttribute('data-dbt-table-page');
            var target = hit.getAttribute('data-dbt-page-target');
            svg.querySelectorAll('[data-dbt-table-page="' + chartId + '"]').forEach(function (page) {
                page.style.display = page.getAttribute('data-page') === target ? '' : 'none';
            });
        });
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
