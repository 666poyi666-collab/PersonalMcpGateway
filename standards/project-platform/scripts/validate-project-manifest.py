from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as handle:
        return cast(dict[str, Any], json.load(handle))


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate-project-manifest.py SCHEMA MANIFEST", file=sys.stderr)
        return 2

    schema_path = Path(sys.argv[1])
    manifest_path = Path(sys.argv[2])
    try:
        schema = load_json(schema_path)
        manifest = load_json(manifest_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        errors = sorted(
            validator.iter_errors(manifest), key=lambda error: list(error.absolute_path)
        )
    except (OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2

    if not errors:
        return 0
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        print(f"{location}: {error.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
