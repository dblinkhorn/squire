# Google Calendar Integration

## Trigger

Calendar creation is triggered by the `cal:` prefix in v1. If a user does not prefix, the system may propose an event but does not create it without confirmation.

## Behavior

When enabled, the system creates an event with default reminders and stores the gcal_event_id. If datetime parsing is low confidence, the system proposes the event and requests confirmation instead of creating it. Calendar integration is optional and disabled by default.

## Future Extensions

Possible extensions include upcoming meeting prep and post-event follow-ups.
