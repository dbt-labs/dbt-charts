#!/usr/bin/env python3
"""Close GitHub issues a just-shipped release's changelog says it fixes.

`.changie.yaml`'s `changeFormat` appends a `[Closes <repo>#N](<issue URL>)`
link to a changelog entry whenever the fragment that produced it set
`custom: {DbtChartsIssue: N}`. `build_marker` compiles the match pattern from
`--repo` at runtime, so this only ever matches a marker naming the exact
repo this script was invoked for.

`changie batch <version>` writes one file per release, `.changes/<version>.md`
(`--changes-dir` default `.changes`), containing only that version's entries;
`changie merge` then concatenates every such file into `CHANGELOG.md`.
Copybara exports `.changes/**` unchanged alongside it. Runs in `release.yml`:
once with `--dry-run` in `build` (fails fast, before publish, if there's no
entry for this release) and once for real in `close-linked-issues`, after the
PyPI publish job.

Exits 0 on success (including "no markers found"), 1 if closing any issue
failed, 2 on a configuration error (bad tag, missing changelog entry).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_TAG_PREFIX = "dbt-charts-v"


def build_marker(repo: str) -> re.Pattern[str]:
    return re.compile(
        rf"\[Closes\s+{re.escape(repo)}#(\d+)\]"
        rf"\(https://github\.com/{re.escape(repo)}/issues/\1\)"
    )


def issues_closed_by(text: str, marker: re.Pattern[str]) -> list[int]:
    return list(dict.fromkeys(int(number) for number in marker.findall(text)))


def close_issue(repo: str, issue: int, release_url: str, *, dry_run: bool) -> None:
    comment = f"Closed by {release_url}."
    if dry_run:
        print(f"[dry-run] would close {repo}#{issue}: {comment}")
        return
    subprocess.run(
        ["gh", "issue", "close", str(issue), "--repo", repo, "--comment", comment],
        check=True,
    )
    print(f"closed {repo}#{issue}: {comment}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changes-dir", type=Path, default=Path(".changes"))
    parser.add_argument(
        "--tag", required=True, help="release tag, e.g. dbt-charts-v0.8.0"
    )
    parser.add_argument(
        "--repo", required=True, help="owner/repo, e.g. dbt-labs/dbt-charts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned closures instead of calling gh",
    )
    args = parser.parse_args(argv)

    if not args.tag.startswith(_TAG_PREFIX):
        print(
            f"::error::tag {args.tag!r} does not start with {_TAG_PREFIX!r}",
            file=sys.stderr,
        )
        return 2
    version = args.tag[len(_TAG_PREFIX) :]

    version_file = args.changes_dir / f"{version}.md"
    if not version_file.is_file():
        print(f"::error::no changelog entry found: {version_file}", file=sys.stderr)
        return 2
    version_text = version_file.read_text(encoding="utf-8")

    issues = issues_closed_by(version_text, build_marker(args.repo))
    if not issues:
        print(f"no linked issues found in {version_file}")
        return 0

    release_url = f"https://github.com/{args.repo}/releases/tag/{args.tag}"
    failed: list[int] = []
    for issue in issues:
        try:
            close_issue(args.repo, issue, release_url, dry_run=args.dry_run)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            failed.append(issue)
            print(
                f"::error::failed to close {args.repo}#{issue}: {exc}", file=sys.stderr
            )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
