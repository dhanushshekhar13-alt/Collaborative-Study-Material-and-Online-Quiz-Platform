"""Guards for the S00.1 acceptance criteria that are about the repository itself."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_ASSIGNMENT = re.compile(
    r"^(AUTH_TOKEN_SECRET|LLM_API_KEY|POSTGRES_PASSWORD)[ \t]*=[ \t]*[^\s#]+", re.MULTILINE
)


def _tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [ROOT / line for line in output.splitlines() if line]


def test_env_file_is_git_ignored():
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()


def test_no_secret_values_are_committed():
    offenders = []
    for path in _tracked_files():
        if path.suffix in {".docx", ".pdf", ".png", ".drawio"} or not path.is_file():
            continue
        if SECRET_ASSIGNMENT.search(path.read_text(errors="ignore")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_readme_setup_fits_in_ten_commands():
    readme = (ROOT / "README.md").read_text()
    setup = readme.split("## Local setup", 1)[1].split("```sh", 1)[1].split("```", 1)[0]
    commands = [line for line in setup.splitlines() if line.strip() and not line.startswith("#")]
    assert 0 < len(commands) <= 10
