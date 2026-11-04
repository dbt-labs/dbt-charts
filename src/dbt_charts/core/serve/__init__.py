"""HTTP server for dbt charts.

Stage: SERVE
Purpose: Provide HTTP endpoints for dashboard rendering.

Routes:
- /health - Health check
- /inspect/{template}/?model=X - Inspect templates
- /{path}/?var=value - Any board file (path.yml → /path/)
"""
