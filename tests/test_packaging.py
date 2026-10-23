"""Packaging consistency: pip requirement exports cover the declared dependencies.

Audit D26: requirements.txt was not regenerated after dependency changes in
Stages 4-6 (highspy missing, seaborn stale). Regenerate both files with
``uv export --no-hashes --no-emit-project --format requirements-txt`` (add
``--no-dev`` for requirements.txt); RUNNING.md has the exact commands.
"""

import re
import tomllib

from tests_helpers import REPO_ROOT


def _names(text: str) -> set[str]:
    return {
        m.group(1).lower().replace("_", "-")
        for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", text, re.MULTILINE)
    }


def _declared(spec: list[str]) -> set[str]:
    return {
        re.split(r"[<>=!~\[ ;]", s, maxsplit=1)[0].lower().replace("_", "-")
        for s in spec
    }


def test_requirement_exports_match_pyproject() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    runtime = _declared(pyproject["project"]["dependencies"])
    dev = _declared(pyproject["dependency-groups"]["dev"])
    req = _names((REPO_ROOT / "requirements.txt").read_text(encoding="utf-8"))
    req_dev = _names((REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8"))
    assert runtime <= req, f"missing from requirements.txt: {runtime - req}"
    assert runtime | dev <= req_dev, (
        f"missing from requirements-dev.txt: {(runtime | dev) - req_dev}"
    )
    assert not (dev - runtime) & req, "dev-only packages leaked into requirements.txt"
