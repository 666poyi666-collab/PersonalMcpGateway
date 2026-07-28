from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as handle:
        return cast(dict[str, Any], json.load(handle))


def sanitized_error_message(error: Any) -> str:
    validator = str(error.validator)
    if validator == "required" and isinstance(error.instance, dict):
        required = cast(list[str], error.validator_value)
        missing = sorted(name for name in required if name not in error.instance)
        return f"missing required properties: {', '.join(missing)}"
    if validator == "minItems":
        return f"must contain at least {error.validator_value} items"
    if validator == "maxItems":
        return f"must contain at most {error.validator_value} items"
    if validator == "minLength":
        return f"must contain at least {error.validator_value} characters"
    if validator == "maxLength":
        return f"must contain at most {error.validator_value} characters"
    if validator == "type":
        return "has the wrong JSON type"
    if validator == "additionalProperties":
        return "contains properties that are not allowed"
    if validator == "const":
        return "does not equal the required constant"
    if validator == "enum":
        return "is not an allowed value"
    if validator == "pattern":
        return "does not match the required pattern"
    if validator == "format":
        return f"is not a valid {error.validator_value}"
    return f"failed {validator} validation"


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
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(manifest),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except (OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2

    if not errors:
        return 0
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        print(f"{location}: {sanitized_error_message(error)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
