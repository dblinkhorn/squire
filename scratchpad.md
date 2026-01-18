Decisions/assumptions:
- Use config.yaml for local configuration; it is git-ignored.
- config.yaml.example is the committed reference template.
- Two-step LLM interpretation (classify then extract) with strict bucket schemas.
- Interpreter falls back to title as next_action for admin items when unclear.

Notes:
- Add bot-side logging (raw id, classification, apply result) later.
- Replace title-based IDs with short random alphanumeric IDs (no extra deps).
