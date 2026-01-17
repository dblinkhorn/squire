from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import yaml

from squire_core.interpreter import interpret_text
from squire_core.llm.openai_provider import OpenAIProvider


_CLASSIFY_PROMPT = (
    "You are Squire's classifier. "
    "Classify the user input into one of: people, projects, ideas, admin, or unknown. "
    "Include a confidence score between 0 and 1. "
    "If the input is ambiguous, set object_type to unknown and keep confidence low. "
    "Return only JSON that matches the provided schema."
)

_EXTRACT_PROMPT = (
    "You are Squire's interpreter. "
    "Extract structured fields required by the schema and include a confidence score between 0 and 1. "
    "If the input is ambiguous, keep confidence low and set unknown fields to null. "
    "Only use fields allowed by the schema for the chosen object_type. "
    "If a field is unknown or not present, include it as null rather than omitting it. "
    "Return only JSON that matches the provided schema."
)


def _load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Interpret text into a DerivedEvent")
    parser.add_argument("text", help="User input to interpret")
    parser.add_argument("--model", help="Model name to use")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
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
    parser.add_argument("--classify-prompt", default="", help="Override classifier prompt")
    parser.add_argument("--extract-prompt", default="", help="Override extraction prompt")
    args = parser.parse_args()

    config = _load_config(args.config)
    model = args.model or config.get("llm", {}).get("interpreter_model")
    if not model:
        raise SystemExit("Model required. Provide --model or set llm.interpreter_model in config.yaml")

    provider = OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY"))
    classify_prompt = args.classify_prompt or config.get("llm", {}).get("classify_prompt") or _CLASSIFY_PROMPT
    extract_prompt = args.extract_prompt or config.get("llm", {}).get("interpreter_prompt") or _EXTRACT_PROMPT

    classification = interpret_text(
        provider=provider,
        text=args.text,
        model=model,
        system_prompt=classify_prompt,
        schema_path=Path(args.classify_schema),
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

    interpretation = interpret_text(
        provider=provider,
        text=args.text,
        model=model,
        system_prompt=extract_prompt,
        schema_path=Path(schema_path),
    )

    print(interpretation.raw_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
