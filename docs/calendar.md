# Google Calendar Integration

## Trigger

Calendar creation can be triggered by the `cal:` prefix or by a high-confidence interpretation that suggests a calendared event. If the user does not prefix, the system proposes the event and asks for confirmation before creating it.

## Behavior

When enabled, the system creates a calendar event with default reminders and stores the gcal_event_id on the admin item. If datetime parsing is low confidence, the system proposes the event and requests confirmation instead of creating it. Calendar integration is optional and disabled by default.

## Future Extensions

Possible extensions include upcoming meeting prep and post-event follow-ups.
