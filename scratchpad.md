Decisions/assumptions:
- Use config.yaml for local configuration; it is git-ignored.
- config.yaml.example is the committed reference template.
- Two-step LLM interpretation (classify then extract) with strict bucket schemas.
- Interpreter falls back to title as next_action for admin items when unclear.
- Prompt overrides are file-based via llm.classify_prompt_path and llm.interpreter_prompt_path.
- Derived events are persisted under events/derived/<raw_event_id>/ with raw model output and error logs.
- IDs use python-ulid for time-sortable ULID strings; raw events are prefixed (e.g., R_).
- Planned: optional GitHub repo creation/backup for the archive storage.
- Next: link raw events to canonical objects (source_event_ids) and add pending action confirmation flow.

Notes:
- Add bot-side logging (raw id, classification, apply result) later.
- Replace title-based IDs with short random alphanumeric IDs (no extra deps).
