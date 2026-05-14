"""Auto-generate Dockerfiles for CodeTin projects."""

from __future__ import annotations

import os

# Language detection heuristics
LANGUAGE_DETECTION: list[tuple[str, list[str]]] = [
    ("python", ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"]),
    ("node", ["package.json", "package-lock.json", "yarn.lock"]),
    ("go", ["go.mod", "go.sum"]),
    ("rust", ["Cargo.toml", "Cargo.lock"]),
    ("ruby", ["Gemfile", "Gemfile.lock", "Rakefile"]),
    ("java", ["pom.xml", "build.gradle", "build.gradle.kts"]),
]

# Dockerfile templates per language
DOCKERFILE_TEMPLATES: dict[str, str] = {
    "python": """\
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true
EXPOSE {port}
CMD ["python", "{entry}"]
""",
    "node": """\
FROM node:20-slim
WORKDIR /app
COPY . .
RUN npm install 2>/dev/null || true
EXPOSE {port}
CMD ["node", "{entry}"]
""",
    "go": """\
FROM golang:1.22-slim
WORKDIR /app
COPY . .
RUN go build -o app . 2>/dev/null || true
EXPOSE {port}
CMD ["./app"]
""",
    "rust": """\
FROM rust:1.75-slim
WORKDIR /app
COPY . .
RUN cargo build --release 2>/dev/null || true
EXPOSE {port}
CMD ["./target/release/app"]
""",
    "ruby": """\
FROM ruby:3.3-slim
WORKDIR /app
COPY . .
RUN bundle install 2>/dev/null || true
EXPOSE {port}
CMD ["ruby", "{entry}"]
""",
    "java": """\
FROM eclipse-temurin:21-jre
WORKDIR /app
COPY . .
EXPOSE {port}
CMD ["java", "-jar", "{entry}"]
""",
}

DEFAULT_PORT: dict[str, int] = {
    "python": 8000,
    "node": 3000,
    "go": 8080,
    "rust": 8000,
    "ruby": 4567,
    "java": 8080,
}

DEFAULT_ENTRY: dict[str, str] = {
    "python": "main.py",
    "node": "index.js",
    "go": "main.go",
    "rust": "src/main.rs",
    "ruby": "main.rb",
    "java": "app.jar",
}


def detect_language(workspace_path: str) -> str:
    """Detect project language from files in workspace."""
    try:
        files = set(os.listdir(workspace_path))
    except OSError:
        return "node"

    for language, markers in LANGUAGE_DETECTION:
        if any(m in files for m in markers):
            return language

    # Check for common file extensions
    for f in files:
        if f.endswith(".py"):
            return "python"
        elif f.endswith((".js", ".ts", ".mjs")):
            return "node"
        elif f.endswith(".go"):
            return "go"
        elif f.endswith(".rs"):
            return "rust"
        elif f.endswith(".rb"):
            return "ruby"
        elif f.endswith(".java"):
            return "java"

    return "node"  # Default


def generate_dockerfile(workspace_path: str, language: str | None = None) -> str:
    """Generate a Dockerfile for the project."""
    if language is None:
        language = detect_language(workspace_path)

    template = DOCKERFILE_TEMPLATES.get(language, DOCKERFILE_TEMPLATES["node"])
    port = DEFAULT_PORT.get(language, 3000)
    entry = DEFAULT_ENTRY.get(language, "index.js")

    return template.format(port=port, entry=entry)
