"""Tests for built-in tools."""

from __future__ import annotations

import os

import pytest


class TestFileTools:
    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        from weather_agents.tools.builtin import _read_file

        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = await _read_file(str(f))
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        from weather_agents.tools.builtin import _read_file

        result = await _read_file("/nonexistent/path.txt")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path):
        from weather_agents.tools.builtin import _write_file

        f = tmp_path / "output.txt"
        result = await _write_file(str(f), "test content")
        assert "Successfully" in result
        assert f.read_text() == "test content"

    @pytest.mark.asyncio
    async def test_write_file_creates_dirs(self, tmp_path):
        from weather_agents.tools.builtin import _write_file

        f = tmp_path / "sub" / "dir" / "file.txt"
        result = await _write_file(str(f), "nested")
        assert "Successfully" in result
        assert f.read_text() == "nested"

    @pytest.mark.asyncio
    async def test_edit_file(self, tmp_path):
        from weather_agents.tools.builtin import _edit_file

        f = tmp_path / "edit.txt"
        f.write_text("hello world")
        result = await _edit_file(str(f), "world", "python")
        assert "Successfully" in result
        assert f.read_text() == "hello python"

    @pytest.mark.asyncio
    async def test_edit_file_text_not_found(self, tmp_path):
        from weather_agents.tools.builtin import _edit_file

        f = tmp_path / "edit2.txt"
        f.write_text("hello")
        result = await _edit_file(str(f), "nonexistent", "replacement")
        assert "Error" in result


class TestSearchTools:
    @pytest.mark.asyncio
    async def test_file_search(self, tmp_path):
        from weather_agents.tools.builtin import _file_search

        (tmp_path / "test.py").write_text("")
        (tmp_path / "test.txt").write_text("")
        result = await _file_search(str(tmp_path), "*.py")
        assert "test.py" in result

    @pytest.mark.asyncio
    async def test_file_search_no_matches(self, tmp_path):
        from weather_agents.tools.builtin import _file_search

        result = await _file_search(str(tmp_path), "*.xyz")
        assert "No files" in result


class TestShellExec:
    @pytest.mark.asyncio
    async def test_basic_command(self):
        from weather_agents.tools.builtin import _shell_exec

        result = await _shell_exec("echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_blocked_command(self):
        from weather_agents.tools.builtin import _shell_exec

        result = await _shell_exec("shutdown now")
        assert "Blocked" in result

    @pytest.mark.asyncio
    async def test_invalid_syntax(self):
        from weather_agents.tools.builtin import _shell_exec

        result = await _shell_exec("echo 'unterminated")
        assert "Invalid" in result or "Error" in result or "hello" not in result

    @pytest.mark.asyncio
    async def test_timeout(self):
        from weather_agents.tools.builtin import _shell_exec

        result = await _shell_exec("sleep 10", timeout=1)
        assert "timed out" in result


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_parse_ddg_results(self):
        from weather_agents.tools.builtin import _parse_ddg_results

        html = '''
        <a class="result__a" href="http://example.com">Example Title</a>
        <a class="result__snippet">This is a snippet</a>
        '''
        results = _parse_ddg_results(html, 5)
        assert len(results) == 1
        assert results[0]["title"] == "Example Title"

    @pytest.mark.asyncio
    async def test_parse_ddg_empty(self):
        from weather_agents.tools.builtin import _parse_ddg_results

        results = _parse_ddg_results("<html>no results</html>", 5)
        assert results == []


class TestHttpTools:
    @pytest.mark.asyncio
    async def test_http_get_invalid_url(self):
        from weather_agents.tools.builtin import _http_get

        result = await _http_get("not-a-url")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_http_post_invalid_url(self):
        from weather_agents.tools.builtin import _http_post

        result = await _http_post("not-a-url", "data")
        assert "Error" in result
