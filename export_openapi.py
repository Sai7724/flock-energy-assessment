"""Export the FastAPI OpenAPI schema to openapi.json in the repo root.

Usage:
    python export_openapi.py

Run this after changing endpoints to keep openapi.json in sync.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app


def main() -> None:
    schema = app.openapi()
    out_path = Path(__file__).parent / "openapi.json"
    out_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported OpenAPI schema to {out_path}")
    print(f"  title:   {schema['info']['title']}")
    print(f"  version: {schema['info']['version']}")
    print(f"  paths:   {len(schema['paths'])} endpoint(s)")


if __name__ == "__main__":
    main()
