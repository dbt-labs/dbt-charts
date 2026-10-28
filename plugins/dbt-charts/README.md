# dbt Charts for Claude

Build, review, and query [dbt Charts](https://dbtcharts.com) boards from Claude.
dbt Charts is a dbt-native board layer: SQL for the data, YAML for the charts,
rendered as HTML and SVG and versioned in git next to your dbt models.

## What the plugin contains

- **The dbtcharts.com connector.** One remote MCP server at
  `https://dbtcharts.com/mcp`, which Claude connects to over HTTPS. You sign
  in to dbt Charts Cloud as yourself the first time Claude uses it (OAuth,
  through your browser); the plugin stores no credentials of its own. The
  connector reads the boards and sources of the projects you can see and runs
  queries against the warehouses those projects connect to, under your own
  permissions.
- **The board authoring skills.** The chart and styling references, plus
  the workflows for building custom charts, designing a report, and
  migrating a dashboard from another BI tool, so Claude follows them without
  being asked. They are the same skills the connector serves on request,
  generated from the dbt Charts package at every release.

The plugin runs no local commands, hooks, or scripts, and sends data only to
the connector above.

## Install

This plugin is for claude.ai and Cowork, where Claude has no shell. An
organization owner distributes it from **Organization settings → Plugins &
skills**, and adds the connector under **Organization settings → Connectors**.
Details:
[Claude and ChatGPT connector](https://docs.dbtcharts.com/cloud/claude-chatgpt-connector/).

## Claude Code and other coding agents

Do not install this plugin there. A repository with the `dct` command line
installed gets its agent skill from `dct init skills`, which writes one `dct`
skill into the repository's agent directories that points Claude Code, Codex,
and Cursor at `dct skills chart`. The agent then works through the CLI, with
no connector or sign-in.
