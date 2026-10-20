---
name: board-review
description: 'Entry point for reviewing a board: runs the structural pass and returns
  a ranked findings list. Use for ''review this dashboard'', ''is this dashboard good'',
  or before delivering a board you just built.'
metadata:
  author: fivetran
---
# Dashboard Review

This host returns no rendered image, so the review is the structural pass alone: run
`get_skill('board-structural-review')` and return its ranked findings list. Judge how
the board looks from the `render_board` result and its warnings, not from
an image.
