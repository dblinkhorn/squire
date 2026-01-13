Decisions/assumptions:
- Canonical objects are the only mutable artifacts and are treated as the truthy state for queries and surfacing.
- Strict derived-event JSON validation rejects extra keys and records derivation_error artifacts.
- Confidence gate defaults to 0.6 with a needs_review path for borderline items.
- Calendar creation requires explicit cal: prefix in v1; otherwise propose and request confirmation.
