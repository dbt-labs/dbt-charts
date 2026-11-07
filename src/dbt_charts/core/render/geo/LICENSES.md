# Bundled map geometry

`us-10m.json` and `world-110m.json` are copied unmodified from the `vega-datasets` 3.2.1 release
(`data/us-10m.json`, `data/world-110m.json`). The `vega-datasets` repository code is
BSD-3-Clause; each dataset keeps the license of its source, recorded in that
release's `datapackage.json`:

| File | Content | Source | License |
|------|---------|--------|---------|
| `us-10m.json` | US states and counties, 1:10,000,000, TopoJSON | [TopoJSON US Atlas](https://github.com/topojson/us-atlas), a redistribution of the US Census Bureau cartographic boundary files | ISC (TopoJSON US Atlas); Census Bureau data is a US government work |
| `nyc-neighborhoods.json` | NYC 2020 Neighborhood Tabulation Areas (262), GeoJSON, simplified | [NYC Open Data 9nt8-h7nd](https://data.cityofnewyork.us/d/9nt8-h7nd), NYC Department of City Planning; retrieved 2026-11-06 | NYC Open Data Terms of Use: public data, free to use without restriction |
| `world-110m.json` | World land and countries, 1:110,000,000, TopoJSON | [TopoJSON World Atlas](https://github.com/topojson/world-atlas), built from Natural Earth | ISC (TopoJSON World Atlas); Natural Earth is public domain |

`nyc-neighborhoods.json` is not an unmodified copy: only the `ntaname` property is kept,
rings are simplified (Douglas-Peucker, 0.00008 degrees) and coordinates are rounded to
four decimals (about 8 m). The export was
`https://data.cityofnewyork.us/api/geospatial/9nt8-h7nd?method=export&format=GeoJSON`.

The ISC License below covers the two vega-datasets files.

ISC License

Copyright 2013-2019 Michael Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
