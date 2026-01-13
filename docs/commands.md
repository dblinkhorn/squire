Discord Interface

Capture

By default, any DM is treated as a note. Explicit prefixes can override capture intent.

Prefixes
- task:
- cal:
- idea:
- person:

Minimal Command Set (v1)

!status shows top due or overdue tasks and the three most recent captures needing attention. !recent [N] shows the last N canonical objects created or updated, with IDs. !find searches title and body via SQLite FTS and returns the top matches with IDs. !show prints a compact view with title, key fields, and the first several lines of the body. !done sets a task status to done and sets completed_at. !append appends text to the body and updates updated_at. !fix field=value [field=value…] modifies frontmatter fields from an allowlist.

Nice-to-Have (v1.1+)

Planned follow-ons include !due [days], !archive, !help, !rebuild-index, and !confirm <pending_action_id> for proposed calendar actions.

Commands never generate new canonical objects unless explicitly stated.
