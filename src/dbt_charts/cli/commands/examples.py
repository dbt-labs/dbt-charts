"""Examples command — thin wrapper over dbt_charts.agent_api.examples."""

from __future__ import annotations

import typer
from rich.padding import Padding
from rich.table import Table

from dbt_charts.agent_api import examples as _api
from dbt_charts.cli._console import dct_console
from dbt_charts.cli._json_output import print_json_result

err_console = dct_console(stderr=True)


def examples_command(
    slug: str | None = None,
    search: str | None = None,
    limit: int = 10,
    as_json: bool = False,
) -> None:
    """List bundled board specimens, search them, or print one by slug."""
    if slug is not None and search is not None:
        err_console.print(
            "[red]Error:[/red] cannot combine an example slug with --search;"
            " use one or the other."
        )
        raise typer.Exit(1)

    if search is not None:
        # Checked here, not by catching the registry's ValueError: that same
        # exception also carries a malformed-specimen packaging failure, and
        # reporting one as "invalid value for --search" blames the query.
        if not search.strip():
            raise typer.BadParameter("query must be non-empty", param_hint="--search")
        result = _api.search_examples(search, limit=limit)
        if as_json:
            print_json_result(result)
            return
        _print_search_results(result)
        return

    if slug is None:
        listing = _api.list_examples()
        if as_json:
            print_json_result(listing)
            return
        _print_examples_table(listing)
        return

    try:
        example = _api.get_example(slug)
    except _api.ExampleNotFound as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if as_json:
        print_json_result(example)
    else:
        print(example.yaml, end="")


def _print_examples_table(result: _api.ExampleList) -> None:
    if not result.examples:
        typer.echo("No examples available.")
        return

    console = dct_console()
    typer.echo("Board specimens — complete, renderable boards to copy and edit.")
    typer.echo(
        "Workflow prose is `dct skills`; the field reference is `dct skills chart`.\n"
    )

    slug_width = max(len(e.slug) for e in result.examples)
    for category in sorted({e.category for e in result.examples}):
        in_category = [e for e in result.examples if e.category == category]
        console.print(f"[bold]{category}[/bold]")
        console.print(Padding(_examples_table(in_category, slug_width), (0, 0, 0, 2)))
        console.print()

    typer.echo("Next steps")
    typer.echo("  dct examples <slug>  Print the full board YAML and edit it")
    typer.echo("  dct skills chart     YAML syntax reference")
    typer.echo("")
    typer.echo('Find a specimen: dct examples -s "<query>"')
    typer.echo(
        "Specimens carry inline data and no `source:` — point the queries at "
        "your own source."
    )


def _examples_table(examples: list[_api.Example], slug_width: int) -> Table:
    t = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    t.add_column("Slug", style="bold", no_wrap=True, width=slug_width)
    t.add_column("Title", overflow="fold")
    t.add_column("Lines", style="dim", justify="right", no_wrap=True)
    for example in examples:
        t.add_row(example.slug, example.title, str(example.line_count))
    return t


def _print_search_results(result: _api.ExampleSearchResult) -> None:
    if not result.hits:
        typer.echo(f'Board specimens — search results for "{result.query}"\n')
        typer.echo(
            'No examples matched. Try `dct examples -s "kpi"`'
            " or `dct examples` (full list)."
        )
        return

    console = dct_console()
    typer.echo(f'Board specimens — search results for "{result.query}"\n')
    for hit in result.hits:
        console.print(
            f"  [bold]{hit.slug}[/bold]  {hit.title}  [dim]score {hit.score:.2f}[/dim]"
        )
        console.print(f"    {_first_sentence(hit.notes)}")
    typer.echo("")
    typer.echo(f"{len(result.hits)} matches. Read one: `dct examples <slug>`.")


def _first_sentence(text: str) -> str:
    """Collapse a YAML block-scalar description to a single sentence."""
    flat = " ".join(text.split())
    head, sep, _ = flat.partition(".")
    return head + sep if sep else flat
