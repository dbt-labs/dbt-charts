# dbt charts — standalone development commands.
#
# This file is the OSS repo's root justfile. It is checked in at
# dbt-charts/oss/justfile and exported by copybara with core.move("oss", "")
# (the whole oss/ directory, this file included) — a real reviewable file,
# not a string-patched projection of dbt-charts/mod.just.

host := env_var_or_default("DCT_HOST", "127.0.0.1")
demo_dir := env_var_or_default("DCT_DEMO_DIR", ".demo")

# One genuinely standalone-incompatible case, re-audited empirically against
# a real export (everything else in the old, much longer list here turned out
# to already pass standalone once dbt-charts self-contained landed):
#   - test_help_option_surface.py's "inspect" case: expects `table`/`audit`,
#     which the private dbt-charts-super-schema plugin registers via an
#     entry-point. Permanent, not temporary -- that package never ships in
#     the OSS wheel. Deselected narrowly (not file-ignored) since the file's
#     other 4 cases pass and cover real OSS-only commands.
monorepo_only := "--deselect=tests/cli/test_help_option_surface.py::test_help_documents_option_surface[inspect]"

# Show the main commands
default:
    @echo "dbt charts — development commands"
    @echo ""
    @echo "━━━ SETUP ━━━"
    @echo "  just install            Install all dependencies into .venv"
    @echo ""
    @echo "━━━ TESTING ━━━"
    @echo "  just test [ARGS]        Full test suite (excludes slow/e2e/network)"
    @echo "  just test-file FILE     Run one test file or node id"
    @echo "  just test-windows       Windows-safe release subset (-m windows)"
    @echo "  just smoke              Build a wheel, install it, render a chart"
    @echo ""
    @echo "━━━ CODE QUALITY ━━━"
    @echo "  just fix                Format + lint with auto-fix"
    @echo "  just lint               Format check, lint, model conventions, vulture"
    @echo "  just typecheck          Static type checking (pyright)"
    @echo "  just tach               Import-boundary checks"
    @echo "  just type-state         Type-state ratchet vs merge-base"
    @echo "  just ci                 lint + typecheck + tach + test"
    @echo ""
    @echo "━━━ RUN ━━━"
    @echo "  just demo [PORT]        Scaffold a demo project and serve it"
    @echo "  just serve DIR [PORT]   Serve an existing project directory"
    @echo ""
    @echo "━━━ GENERATED ARTIFACTS ━━━"
    @echo "  just gen                Regenerate every introspection-derived artifact"
    @echo ""
    @echo "Run 'just --list' for every recipe."

# ============================================
# Setup
# ============================================

# Install all dependencies (every warehouse adapter, mcp, server extras)
# --locked: uv.lock is a committed artifact of the monorepo export, so a lock
# that no longer matches pyproject.toml is a bug to surface, not to re-resolve.
install:
    uv sync --all-extras --locked
    @echo "✅ Ready. Run 'just test' or 'just demo'."

# ============================================
# Testing
# ============================================

# Run the test suite (excludes slow and e2e tests)
test *ARGS: test-windows
    uv run pytest -q --tb=short tests -n auto -m "not slow and not e2e" {{monorepo_only}} {{ARGS}}

# Run one test file or node id
test-file FILE *ARGS:
    uv run pytest -q --tb=short {{FILE}} {{ARGS}}

# Windows-safe release subset. LC_ALL=C + PYTHONUTF8=0 makes a missing
# encoding= raise off Windows, which is the point of the subset.
# -p no:tach: the plugin dies on the non-ASCII pyproject.
test-windows *ARGS:
    LC_ALL=C PYTHONUTF8=0 uv run pytest -q --tb=short -p no:tach tests -m windows {{monorepo_only}} {{ARGS}}

# The CI pipeline that produces this export runs this same lock install on
# its own nightly cron, but a dbt-charts-v* tag build runs off-cycle and
# needs its own proof the committed oss/uv.lock installs and the wheel
# Copybara's path rewrites (core.move/core.replace) produce is genuinely
# installable. Wheel/render steps reuse the fixture and commands from the
# monorepo's own smoke-install CI job (.github/workflows/test-dataface.yml).
smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    uv sync --all-extras --locked
    rm -rf dist .smoke-venv
    uv build --wheel
    uv venv .smoke-venv
    uv pip install --python .smoke-venv/bin/python dist/*.whl
    export DCT_NO_WORKSPACE_GUARD=1  # smoke-installs a wheel from inside this checkout on purpose
    dct="$(pwd)/.smoke-venv/bin/dct"
    "$dct" --help >/dev/null
    "$dct" --version
    (cd tests/fixtures/oss-smoke && "$dct" render sample.yml)
    echo "✅ smoke: package builds, installs, and renders"

# ============================================
# Code quality
# ============================================

# Format and lint with auto-fix
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Format check, lint, model-convention check, and dead-code scan
lint:
    @echo "▶ format"
    uv run ruff format --check --quiet .
    @echo "▶ lint"
    uv run ruff check --quiet .
    @echo "▶ model conventions"
    uv run python scripts/check_models.py
    @echo "▶ dead code (vulture)"
    uv run vulture

# Static type checking (pyright; config in pyrightconfig.json).
typecheck:
    uv run pyright

# Import-boundary checks. `check` enforces internal layering; `check-external`
# enforces the wheel-outbound allowlist against declared dependencies.
# Config lives in tach.toml. Requires `tach` in the dev dependency group.
tach:
    uv run tach check
    uv run tach check-external

# Merge-base ratchet: blocks unmarked growth of silent_fallback / cast /
# type_ignore / object_annotation / explicit_any in src/dbt_charts/core.
type-state *ARGS:
    uv run python scripts/type_state_gate.py {{ARGS}}

# Everything CI gates on
ci: lint typecheck tach test

# ============================================
# Run
# ============================================

# Scaffold a demo project under .demo/ and serve it
demo port="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f "{{demo_dir}}/dbt_charts.yml" ]; then
        mkdir -p "{{demo_dir}}"
        uv run dct init --project-dir "{{demo_dir}}"
    fi
    port_args=""
    if [ -n "{{port}}" ]; then port_args="--port {{port}}"; fi
    uv run dct serve --project-dir "{{demo_dir}}" --host {{host}} $port_args

# Serve an existing project directory
serve dir port="":
    #!/usr/bin/env bash
    set -euo pipefail
    port_args=""
    if [ -n "{{port}}" ]; then port_args="--port {{port}}"; fi
    uv run dct serve --project-dir "{{dir}}" --host {{host}} $port_args

# ============================================
# Generated artifacts
# ============================================

# Regenerate every introspection-derived artifact.
gen: gen-highlight-artifacts

# Regenerate the highlight manifest (board.json) and tmLanguage grammar.
# Re-run after any change to the authored schema's key structure.
gen-highlight-artifacts:
    uv run python scripts/gen_highlight_artifacts.py

# Freeze a changed YAML grammar under the same version as its dbt charts
# release. Existing snapshots are verified and never rewritten; an unchanged
# grammar renumbers the DEV entry to the next predicted version instead of
# freezing one.
freeze-yaml-schema version released_at:
    uv run python -m dbt_charts.schema_release {{version}} {{released_at}}
