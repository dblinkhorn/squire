# Repository Layout

The git repository is the contract. This layout shows the intended top-level structure and locations for prompts, schemas, and derived artifacts.

```
repo/
  config.yaml
  config/
    prompts/
      classify_v1.txt
      digest_daily_v1.txt
      review_weekly_v1.txt
      calendar_parse_v1.txt
    schemas/
      derived_event_classify_v1.json
      derived_event_people_v1.json
      derived_event_projects_v1.json
      derived_event_ideas_v1.json
      derived_event_admin_v1.json
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
      A_<uuid>.md
    ideas/
      I_<uuid>.md
    people/
      P_<uuid>.md
    projects/
      PR_<uuid>.md
  index/
    sb.sqlite
  docs/
  packages/
  deploy/
```
