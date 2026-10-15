"""Tests for agent_api.file_refs — expand_file_refs and ExpandedPrompt.

Covers: in-cwd expansion, out-of-cwd rejection, oversized truncation,
binary file passthrough, email-containing text passthrough, parent-traversal
segment rejection, non-existent path passthrough, fence collisions, and the
stat-first OOM guard.
"""

from __future__ import annotations

from pathlib import Path

from dbt_charts.agent_api.file_refs import (
    ExpandedPrompt,
    FileRef,
    expand_file_refs,
)


class TestExpandFileRefs:
    def test_single_ref_returns_expanded_prompt(self, tmp_path: Path) -> None:
        (tmp_path / "foo.yml").write_text("title: Foo\n")
        result = expand_file_refs("review @foo.yml", project_dir=tmp_path)
        assert isinstance(result, ExpandedPrompt)
        assert "title: Foo" in result.text
        assert "@foo.yml" not in result.text
        assert len(result.references) == 1
        ref = result.references[0]
        assert isinstance(ref, FileRef)
        assert ref.token == "@foo.yml"
        assert ref.path == (tmp_path / "foo.yml").resolve()

    def test_in_cwd_file_expanded_as_fenced_block(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        target.write_text("# Hello\nWorld\n")
        result = expand_file_refs("see @notes.md for context", project_dir=tmp_path)
        assert "```" in result.text
        assert "# Hello" in result.text
        assert "@notes.md" not in result.text

    def test_no_refs_returns_empty_references(self, tmp_path: Path) -> None:
        result = expand_file_refs("plain message", project_dir=tmp_path)
        assert result.text == "plain message"
        assert result.references == []

    def test_nonexistent_file_not_in_references(self, tmp_path: Path) -> None:
        result = expand_file_refs("look at @ghost.yml", project_dir=tmp_path)
        assert "@ghost.yml" in result.text
        assert result.references == []

    def test_multiple_refs_all_recorded(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("file A content")
        (tmp_path / "b.txt").write_text("file B content")
        result = expand_file_refs("compare @a.txt and @b.txt", project_dir=tmp_path)
        assert "file A content" in result.text
        assert "file B content" in result.text
        assert "@a.txt" not in result.text
        assert "@b.txt" not in result.text
        tokens = {r.token for r in result.references}
        assert tokens == {"@a.txt", "@b.txt"}

    def test_out_of_cwd_not_in_references(self, tmp_path: Path) -> None:
        cwd = tmp_path / "work"
        cwd.mkdir()
        (tmp_path / "outside.txt").write_text("secret")
        result = expand_file_refs("look at @../outside.txt", project_dir=cwd)
        assert "@../outside.txt" in result.text
        assert result.references == []

    def test_binary_file_not_in_references(self, tmp_path: Path) -> None:
        (tmp_path / "data.bin").write_bytes(bytes(range(256)))
        result = expand_file_refs("embed @data.bin here", project_dir=tmp_path)
        assert "@data.bin" in result.text
        assert result.references == []

    def test_email_address_preserved(self, tmp_path: Path) -> None:
        result = expand_file_refs(
            "send results to dave@example.com please", project_dir=tmp_path
        )
        assert "dave@example.com" in result.text
        assert result.references == []

    def test_parent_traversal_segment_rejected(self, tmp_path: Path) -> None:
        result = expand_file_refs("look at @foo/../bar.md ok", project_dir=tmp_path)
        assert "@foo/../bar.md" in result.text
        assert result.references == []

    def test_oversized_file_stubbed(self, tmp_path: Path) -> None:
        """Files > 200 KB produce a stub with first 50 lines, not the full file."""
        big_file = tmp_path / "big.txt"
        # Each line is ~25 chars, 10000 lines ≈ 250 KB.
        lines = [f"line {i:05d}: some padding here" for i in range(10000)]
        big_file.write_text("\n".join(lines))

        result = expand_file_refs("analyze @big.txt", project_dir=tmp_path)
        assert "KB" in result.text or "lines" in result.text
        # First 50 lines present; line 51 onward (and the very tail) absent.
        assert "line 00000" in result.text
        assert "line 00049" in result.text
        assert "line 09999" not in result.text
        assert len(result.references) == 1
        assert result.references[0].token == "@big.txt"

    def test_oversized_file_does_not_read_full_content_into_memory(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Stat-first guard: a multi-MB file must NOT trigger a full read.

        Regression for an OOM where read_bytes() was called before the size
        check. Spies on Path.read_bytes and asserts it is never called for
        the oversized path.
        """
        big_file = tmp_path / "big.txt"
        big_file.write_bytes(b"line of text\n" * 400_000)  # 5 MB

        read_bytes_calls: list[Path] = []
        original_read_bytes = Path.read_bytes

        def spy_read_bytes(self):
            read_bytes_calls.append(self)
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)

        result = expand_file_refs("analyze @big.txt", project_dir=tmp_path)
        assert "KB" in result.text
        assert "line of text" in result.text
        assert big_file not in read_bytes_calls

    def test_file_in_subdirectory_expanded(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "design.md").write_text("# Design\nContent here.\n")
        result = expand_file_refs(
            "read @docs/design.md for context", project_dir=tmp_path
        )
        assert "# Design" in result.text
        assert "@docs/design.md" not in result.text

    def test_at_sign_in_middle_of_word_preserved(self, tmp_path: Path) -> None:
        """word@foo is NOT an @file reference (negative lookbehind on \\w)."""
        (tmp_path / "foo").write_text("should not appear")
        result = expand_file_refs("my-var@foo is a thing", project_dir=tmp_path)
        assert "should not appear" not in result.text
        assert "my-var@foo" in result.text

    def test_trailing_period_stripped_and_file_expands(self, tmp_path: Path) -> None:
        """@notes.md. (end of sentence) expands @notes.md, keeps trailing dot."""
        (tmp_path / "notes.md").write_text("# Notes\nHello.\n")
        result = expand_file_refs("read @notes.md. for context", project_dir=tmp_path)
        assert "# Notes" in result.text
        assert "@notes.md" not in result.text
        assert "for context" in result.text

    def test_fence_collision_uses_longer_fence(self, tmp_path: Path) -> None:
        """File content with ``` must be wrapped in a longer fence."""
        (tmp_path / "doc.md").write_text("intro\n```\ncode block\n```\nouter\n")
        result = expand_file_refs("read @doc.md", project_dir=tmp_path)
        assert "````" in result.text
        assert "```\ncode block\n```" in result.text

    def test_fence_collision_handles_longer_inner_runs(self, tmp_path: Path) -> None:
        """File with a 5-backtick run gets a 6+-backtick fence."""
        (tmp_path / "doc.md").write_text("````` weird `````")
        result = expand_file_refs("read @doc.md", project_dir=tmp_path)
        assert "``````" in result.text

    def test_path_resolving_outside_cwd_preserved(self, tmp_path: Path) -> None:
        """Absolute-path token resolves outside project_dir → not inlined."""
        cwd = tmp_path / "work"
        cwd.mkdir()
        sibling = tmp_path / "sibling.txt"
        sibling.write_text("secret data")
        abs_token = str(sibling).lstrip("/")
        result = expand_file_refs(f"look at @{abs_token}", project_dir=cwd)
        assert "secret data" not in result.text
        assert result.references == []
