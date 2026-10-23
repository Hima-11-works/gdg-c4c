# Gemini photo review demonstration

This runbook demonstrates the existing Gemini visual advisory in the dashboard's
citizen-photo review queue. It is a reviewer aid: it does not establish that a
pollution event occurred, estimate concentration, or make the final decision.

## Before the demo

1. Configure the backend Gemini API key and model in the deployment environment.
   Never put the key in the browser bundle or a demo recording.
2. Submit three consented, non-sensitive photos through the citizen app:
   - a clear smoke-plume example;
   - a visually ambiguous example (for example, steam or haze);
   - an unrelated scene with no apparent plume.
3. Confirm the reviewer role can open the protected photo derivative and the
   evidence queue. Use the same photo for the model advisory and the human
   review so the comparison is meaningful.

## Demonstration sequence

For each report, show the report location and timestamp, open the sanitized
photo derivative, affirm the per-photo consent checkbox, and request the
Gemini advisory. Show the event category, visible observations, visual support,
missing information, uncertainty, model ID, prompt version, and analysis time.
Then record the human review decision separately. Keep the advisory on screen
beside the final decision to make the human-in-the-loop boundary clear.

Do not describe the model output as a verified source, a measured pollutant
value, or a dispatch instruction. If the model is unavailable, show the error
and continue with manual review rather than substituting a canned result.

## Pilot evidence to collect

For each case, record the evidence ID, case category, advisory availability,
reviewer decision, whether the advisory changed the reviewer's initial view,
and elapsed time from opening the photo to recording the decision. Record the
reviewer's initial view before showing Gemini's output. Include all three
cases, including failures and disagreements; do not report a model accuracy
score from this small demonstration set.

For a useful pilot comparison, have at least two reviewers assess the same
consented cases, once without the advisory and once with it in randomized
order. Report sample counts, median review time, agreement, and disagreements.
Treat the result as usability evidence only until a larger, independently
labelled evaluation exists.
