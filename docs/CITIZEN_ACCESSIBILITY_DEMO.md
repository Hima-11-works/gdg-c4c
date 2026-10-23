# Citizen language, voice, and low-connectivity demo

The Air Health citizen app supports English, Hindi, and Punjabi for its
citizen-report entry point and report form. Open **Settings → Language &
accessibility** to switch languages. The selected language is stored on-device
and restored the next time the app opens.

On Android, open **Report fire** and use the microphone icon in the optional
notes field. The operating-system speech recognizer handles the short dictation
and returns text to the form. The user taps the control before microphone
access is requested. Speech recognition may use a network connection; audio is
not uploaded to the Air Health API or attached to the submitted report. If a
device has no recognizer or does not support the selected language, typed notes
remain available.

When GPS is unavailable, the report form asks for latitude and longitude instead
of silently using the fallback demo location. A city/region label can also be
entered. The coordinates are checked against geographic bounds before a report
is stored or submitted. With no backend configured, the existing local report
store remains available from the report entry point; photos remain optional and
are size-limited and resized before upload.

The language support is intentionally focused on the citizen reporting journey;
the rest of the dashboard still contains English copy. Verify the focused flow
with:

```bash
cd partner_apps/air_health_flutter
flutter pub get --enforce-lockfile
flutter test
flutter analyze
```
