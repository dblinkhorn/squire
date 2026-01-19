from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from squire_core.config_utils import load_config, normalize_archive_config
from squire_core.derived_event_store import write_derived_event
from squire_core.id_utils import generate_prefixed_id
from squire_core.interpreter import InterpretationValidationError, interpret_text
from squire_core.llm.openai_provider import OpenAIProvider
from squire_core.llm.prompts import load_prompt
from squire_core.timezone_utils import (
    format_reference_date,
    format_reference_time,
    format_reference_weekday,
    resolve_timezone,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interpret text into a DerivedEvent")
    parser.add_argument("text", help="User input to interpret")
    parser.add_argument("--model", help="Model name to use")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply proposed operations to canonical objects",
    )
    parser.add_argument(
        "--classify-schema",
        default="config/schemas/derived_event_classify_v1.json",
        help="Path to classifier schema",
    )
    parser.add_argument(
        "--schema-people",
        default="config/schemas/derived_event_people_v1.json",
        help="Path to people derived schema",
    )
    parser.add_argument(
        "--schema-projects",
        default="config/schemas/derived_event_projects_v1.json",
        help="Path to projects derived schema",
    )
    parser.add_argument(
        "--schema-ideas",
        default="config/schemas/derived_event_ideas_v1.json",
        help="Path to ideas derived schema",
    )
    parser.add_argument(
        "--schema-admin",
        default="config/schemas/derived_event_admin_v1.json",
        help="Path to admin derived schema",
    )
    parser.add_argument(
        "--canonical-schema",
        default="config/schemas/canonical_object_v1.json",
        help="Path to canonical schema",
    )
    parser.add_argument("--classify-prompt-path", default="", help="Path to classifier prompt")
    parser.add_argument("--extract-prompt-path", default="", help="Path to extraction prompt")
    args = parser.parse_args()

    config = load_config(args.config)
    config = normalize_archive_config(config)
    model = args.model or config.get("llm", {}).get("interpreter_model")
    if not model:
        raise SystemExit("Model required. Provide --model or set llm.interpreter_model in config.yaml")

    provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))
    classify_prompt_path = args.classify_prompt_path or config.get("llm", {}).get("classify_prompt_path")
    extract_prompt_path = args.extract_prompt_path or config.get("llm", {}).get("interpreter_prompt_path")
    if not classify_prompt_path or not extract_prompt_path:
        raise SystemExit("Prompt paths required. Set llm.classify_prompt_path and llm.interpreter_prompt_path.")
    try:
        classify_prompt = load_prompt(classify_prompt_path)
        extract_prompt = load_prompt(extract_prompt_path)
    except OSError as exc:
        raise SystemExit(f"Failed to load prompt files: {exc}") from exc

    derived_root = config.get("paths", {}).get("events_derived", "events/derived")
    run_id = generate_prefixed_id("CLI_")

    tz_name = config.get("timezone")
    tz = resolve_timezone(tz_name)
    reference = (
        f"Reference date: {format_reference_date(tz)}. "
        f"Reference weekday: {format_reference_weekday(tz)}. "
        f"Reference time: {format_reference_time(tz)}."
    )
    extract_prompt = f"{extract_prompt} {reference}"

    try:
        classification = interpret_text(
            provider=provider,
            text=args.text,
            model=model,
            system_prompt=classify_prompt,
            schema_path=Path(args.classify_schema),
        )
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=run_id,
            label="invalid",
            error=exc,
        )
        print(exc.raw_text)
        print("Classifier output failed schema validation.")
        return 2
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=run_id,
            label="invalid",
            error=exc,
        )
        print(f"Interpretation failed: {exc}")
        return 2

    write_derived_event(
        derived=classification.derived,
        raw_text=classification.raw_text,
        derived_root=derived_root,
        raw_event_id=run_id,
        label="classify",
    )
    object_type = classification.derived.get("object_type")
    if not isinstance(object_type, str):
        print(classification.raw_text)
        print("Missing or invalid object_type.")
        return 2
    confidence = classification.derived.get("confidence", 0)

    threshold = config.get("confidence", {}).get("create_threshold", 0.6)
    if object_type == "unknown" or confidence < threshold:
        print(classification.raw_text)
        print("Low confidence. Ask for clarification or provide an explicit prefix.")
        return 2

    schema_map = {
        "people": args.schema_people,
        "projects": args.schema_projects,
        "ideas": args.schema_ideas,
        "admin": args.schema_admin,
    }
    schema_path = schema_map.get(object_type)
    if not schema_path:
        print(classification.raw_text)
        print("Unrecognized object_type.")
        return 2

    try:
        interpretation = interpret_text(
            provider=provider,
            text=args.text,
            model=model,
            system_prompt=extract_prompt,
            schema_path=Path(schema_path),
        )
    except InterpretationValidationError as exc:
        write_derived_event(
            derived=exc.payload,
            raw_text=exc.raw_text,
            derived_root=derived_root,
            raw_event_id=run_id,
            label="invalid",
            error=exc,
        )
        print(exc.raw_text)
        print("Interpreter output failed schema validation.")
        return 2
    except Exception as exc:
        write_derived_event(
            derived=None,
            raw_text="",
            derived_root=derived_root,
            raw_event_id=run_id,
            label="invalid",
            error=exc,
        )
        print(f"Interpretation failed: {exc}")
        return 2

    write_derived_event(
        derived=interpretation.derived,
        raw_text=interpretation.raw_text,
        derived_root=derived_root,
        raw_event_id=run_id,
        label="derived",
    )

    if args.apply:
        from squire_core.operation_apply import apply_operations

        objects_root = config.get("paths", {}).get("objects_root", "objects")
        result = apply_operations(
            interpretation.derived,
            objects_root=objects_root,
            canonical_schema_path=Path(args.canonical_schema),
            derived_schema_path=Path(schema_path),
        )
        for path in result.written_paths:
            print(f"Wrote: {path}")
        return 0

    print(interpretation.raw_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
