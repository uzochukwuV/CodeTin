"""File operation tools for agents."""

from __future__ import annotations

import glob
import os
from pydantic import BaseModel, Field

from orchestrator.agents.tools.base import Tool, ToolResult


class ReadFileInput(BaseModel):
    path: str = Field(description="Path to the file to read")


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file. Returns the full text content."
    input_schema = ReadFileInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, path: str) -> ToolResult:
        full_path = self._resolve(path)
        if not os.path.isfile(full_path):
            return ToolResult(success=False, error=f"File not found: {path}")
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            text, truncated = self.truncate(content)
            return ToolResult(success=True, output=text, truncated=truncated)
        except UnicodeDecodeError:
            return ToolResult(success=False, error="Binary file, cannot read as text")
        except PermissionError:
            return ToolResult(success=False, error=f"Permission denied: {path}")

    def _resolve(self, path: str) -> str:
        full = os.path.normpath(os.path.join(self.work_dir, path))
        if not full.startswith(os.path.normpath(self.work_dir)):
            raise ValueError(f"Path traversal detected: {path}")
        return full


class WriteFileInput(BaseModel):
    path: str = Field(description="Path to the file to write")
    content: str = Field(description="File content")


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to a file. Creates parent directories if needed. Overwrites existing files."
    input_schema = WriteFileInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, path: str, content: str) -> ToolResult:
        full_path = self._resolve(path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(success=True, output=f"Written {len(content)} bytes to {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _resolve(self, path: str) -> str:
        full = os.path.normpath(os.path.join(self.work_dir, path))
        if not full.startswith(os.path.normpath(self.work_dir)):
            raise ValueError(f"Path traversal detected: {path}")
        return full


class EditFileInput(BaseModel):
    path: str = Field(description="Path to the file to edit")
    old_text: str = Field(description="Text to replace")
    new_text: str = Field(description="Replacement text")


class EditFileTool(Tool):
    name = "edit_file"
    description = "Surgically replace text in a file. old_text must match exactly (including whitespace)."
    input_schema = EditFileInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, path: str, old_text: str, new_text: str) -> ToolResult:
        full_path = self._resolve(path)
        if not os.path.isfile(full_path):
            return ToolResult(success=False, error=f"File not found: {path}")
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            if old_text not in content:
                return ToolResult(success=False, error=f"old_text not found in {path}")
            new_content = content.replace(old_text, new_text, 1)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return ToolResult(success=True, output=f"Edited {path} successfully")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _resolve(self, path: str) -> str:
        full = os.path.normpath(os.path.join(self.work_dir, path))
        if not full.startswith(os.path.normpath(self.work_dir)):
            raise ValueError(f"Path traversal detected: {path}")
        return full


class ListFilesInput(BaseModel):
    path: str = Field(description="Directory path to list (default: root)")


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files and directories in a path. Returns name, type, and size."
    input_schema = ListFilesInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, path: str = "") -> ToolResult:
        target = os.path.join(self.work_dir, path) if path else self.work_dir
        if not os.path.isdir(target):
            return ToolResult(success=False, error=f"Directory not found: {path or '.'}")
        try:
            entries = []
            for entry in sorted(os.listdir(target)):
                full = os.path.join(target, entry)
                if entry.startswith("."):
                    # Only include key config files
                    if entry not in (".env", ".gitignore", "package.json", "requirements.txt", "go.mod", "Cargo.toml", "pyproject.toml"):
                        continue
                entry_info = {
                    "name": entry,
                    "type": "directory" if os.path.isdir(full) else "file",
                }
                if os.path.isfile(full):
                    entry_info["size"] = os.path.getsize(full)
                entries.append(entry_info)
            output = "\n".join(f"{e['type']:9s} {e['size']:>8}  {e['name']}" if "size" in e else f"{e['type']:9s}         {e['name']}" for e in entries)
            return ToolResult(success=True, output=output)
        except PermissionError:
            return ToolResult(success=False, error=f"Permission denied: {path}")


class GlobInput(BaseModel):
    pattern: str = Field(description="Glob pattern (e.g. '**/*.py', 'src/**/*.ts')")


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern. Returns matching file paths."
    input_schema = GlobInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, pattern: str) -> ToolResult:
        full_pattern = os.path.join(self.work_dir, pattern)
        try:
            matches = glob.glob(full_pattern, recursive=True)
            matches = [os.path.relpath(m, self.work_dir) for m in sorted(matches)]
            output = "\n".join(matches) if matches else "No matches found"
            text, truncated = self.truncate(output)
            return ToolResult(success=True, output=text, truncated=truncated)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class GrepInput(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default="", description="Directory to search in (default: root)")
    glob_pattern: str = Field(default="*", description="File glob to filter (default: all)")


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with regex. Returns matching lines with file:line references."
    input_schema = GrepInput

    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    async def execute(self, pattern: str, path: str = "", glob_pattern: str = "*") -> ToolResult:
        import re
        search_dir = os.path.join(self.work_dir, path) if path else self.work_dir
        if not os.path.isdir(search_dir):
            return ToolResult(success=False, error=f"Directory not found: {path or '.'}")

        try:
            regex = re.compile(pattern)
            matches = []
            for root, dirs, files in os.walk(search_dir):
                # Skip hidden and common non-source dirs
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", "dist", "build")]
                for fname in files:
                    if not self._matches_glob(fname, glob_pattern):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            for i, line in enumerate(f, 1):
                                if regex.search(line):
                                    rel = os.path.relpath(fpath, self.work_dir)
                                    matches.append(f"{rel}:{i}: {line.rstrip()}")
                    except (UnicodeDecodeError, PermissionError):
                        continue

            output = "\n".join(matches) if matches else "No matches found"
            text, truncated = self.truncate(output)
            return ToolResult(success=True, output=text, truncated=truncated)
        except re.error as e:
            return ToolResult(success=False, error=f"Invalid regex: {e}")

    @staticmethod
    def _matches_glob(filename: str, pattern: str) -> bool:
        import fnmatch
        if pattern == "*":
            return True
        return fnmatch.fnmatch(filename, pattern)
