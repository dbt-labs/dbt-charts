# pyright: reportUndefinedVariable=false, reportUnusedExpression=false
# vulture-whitelist.py — confirmed dynamic-name false positives.
#
# Each entry MUST have a `# WHY:` line that names the specific dynamic-dispatch
# surface (Django `clean_<field>`, lark `visit_<rule>`, registry `_register("name")`,
# etc.). If you can't write one, the symbol is probably dead — delete it instead
# of whitelisting it.
#
# This file is input to vulture, not runnable Python — entries are bare names
# scattered across the codebase, so the file is not importable. Vulture's own
# `--make-whitelist` emits the same bare-name format. Ruff B018/F821 are
# suppressed for this path in ruff.toml [lint.per-file-ignores].

# === Test-mock kwarg contracts ===
# WHY: monkeypatched in via `setattr(module, "_review_visual_parity", ...)`;
# the real `_review_visual_parity` is called with these kwargs by chart_lab
# compare code, so the fake must accept the same kwarg names.
source_png  # tests/integration/test_chart_lab_compare.py:164
chosen_mark_type  # tests/integration/test_chart_lab_compare.py:164


# === Duck-typed adapter stub (tests/core/inspect/test_sources_dbt.py) ===
# WHY: this stub's execute(sql, fetch) matches a real warehouse adapter's
# execute() signature for duck-typing purposes; the stub always returns the
# same fixed result regardless of fetch.
fetch  # tests/core/inspect/test_sources_dbt.py:793 (stub execute())

# === PyYAML emitter override (src/dbt_charts/core/compile/authoring/yaml_patch.py) ===
# WHY: PyYAML's emitter calls `increase_indent(flow=..., indentless=...)` by
# keyword, so the override must keep the parameter name though it always
# indents a sequence under its key.
indentless  # src/dbt_charts/core/compile/authoring/yaml_patch.py (_IndentedDumper)
