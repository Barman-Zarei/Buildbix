"""Coordinator: deterministic keyword routing to specialised agents; each agent's action has an approval tier."""
import re

# agent -> (permission action, mutating?)
AGENTS = {"analyzer": "analyze_project", "test": "run_tests", "ci": "generate_config", "code": "modify_project_files",
          "docs": "modify_project_files"}


def route(message: str) -> str:
    m = message.lower()
    if re.search(r"\b(ci|workflow|pipeline|github actions?)\b", m):
        return "ci"
    if re.search(r"\b(readme|docs?|documentation)\b", m):
        return "docs"
    if re.search(r"\btests?\b", m) and not re.search(r"\b(add|write|create|implement)\b", m):
        return "test"
    if re.search(r"\b(add|write|change|fix|implement|refactor|create|modify|update|remove|rename)\b", m):
        return "code"
    return "analyzer"
