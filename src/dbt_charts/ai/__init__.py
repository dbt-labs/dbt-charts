"""Core AI utilities for dbt charts.

This module provides shared AI service utilities used by both Playground and Suite.
It consolidates common patterns like prompt loading, tool dispatch, and YAML extraction.

Modules:
    prompts: Prompt loading and building utilities
    tool_schemas: Canonical tool definitions (render_board, execute_sql, ...)
    tools: Tool-call dispatch over those definitions
    yaml_utils: YAML extraction from AI responses
"""

from dbt_charts.ai.prompts import load_prompt
from dbt_charts.ai.tools import handle_tool_call
from dbt_charts.ai.yaml_utils import extract_yaml

__all__ = [
    # Prompts
    "load_prompt",
    # Tools
    "handle_tool_call",
    # YAML utilities
    "extract_yaml",
]
