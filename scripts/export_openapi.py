"""Write the versioned API contract to docs/api/openapi-v1.json.

Run after changing any route: ``python scripts/export_openapi.py``. CI fails if the
committed contract and the application disagree.
"""

import json
from pathlib import Path

from app.main import create_app

CONTRACT = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi-v1.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT.write_text(render())
    print(f"Wrote {CONTRACT}")
