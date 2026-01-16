# Repository Layout

The git repository is the contract. This layout shows the intended top-level structure and locations for prompts, schemas, and derived artifacts.

```
repo/
  config.yaml
  prompts/
    classify_v1.txt
    digest_daily_v1.txt
    review_weekly_v1.txt
    calendar_parse_v1.txt
  schemas/
    derived_event_v1.json
    canonical_object_v1.json
  events/
    raw/
      <raw_event_id>.md
    derived/
      <raw_event_id>/
        derived_v1_<timestamp>.json
        raw_model_output.txt
        errors.json
  objects/
    admin/
      A_<id>.md
    ideas/
      I_<id>.md
    people/
      P_<id>.md
    projects/
      PR_<id>.md
  index/
    sb.sqlite
  docs/
  packages/
  deploy/
```
