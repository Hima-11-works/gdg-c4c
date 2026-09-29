# F2 threat model — citizen photo evidence

Scope: `POST /reports/{id}/evidence`, `GET /reports/{id}/evidence`,
`GET|POST|DELETE /reports/{id}/evidence/{eid}*`. Written alongside the
implementation; where the two disagree, the code is the specification and this
document is the bug.

## What the attacker controls

Everything up to the storage boundary. The upload endpoint takes an
**unauthenticated** request — the platform has no accounts — so the attacker is
"anyone who can reach the API and knows a report id". Report ids are small
sequential integers, so "knows a report id" is not a meaningful barrier. Every
control below is therefore written against a caller who is actively trying.

The caller's levers: the request body, the multipart part names, the
`Content-Type` of each part, the declared filename, the declared content
length, every byte of the file, the `consent` flag, and the timing and
ordering of repeated requests.

## Assets

1. **The reviewer's screen.** A reviewer looking at a derivative decides
   whether a fire is real. Deceiving that screen corrupts the review queue.
2. **The private object store.** Every accepted byte is stored. Unbounded growth is an
   availability problem for the whole API.
3. **The uploader's privacy.** A photo carries location, time and sometimes
   faces. The derivative is shown to reviewers; the original must not be.
4. **The model's integrity.** A photo must never change modeled air quality.

## Threats, and what answers them

### Malicious files

| Threat | Control | Where |
|---|---|---|
| Executable or script uploaded as a "photo" | **Content sniffed by bytes**, never by `Content-Type`. Only JPEG/PNG/WebP are accepted. | `domain/media_validation.inspect_bytes` |
| Polyglot (a valid header glued to something else) | Both the **leading signature and the trailing terminator** must be present. A four-byte magic number is free to forge; requiring the end of the container is not. | `_has_jpeg_terminator`, `_has_png_terminator` |
| Declared-size lie to force a huge allocation | The header window is a fixed 64 KB and is never derived from the payload. WebP's own RIFF size field is compared against the limit. | `HEADER_WINDOW_BYTES`, `_webp_declared_size` |
| Decompression bomb | Pillow's own guard *and* an explicit 80 MP ceiling, mapped to the same 422. | `build_derivative`, `MAX_DECODED_PIXELS` |
| Image codec exploit | The image is decoded in Pillow, inside the process. This is the accepted residual risk: it is a parser bug in a mature library, and the alternative (sniffing only) is what makes the first two rows above necessary. | — |
| Malware scanning | **Not implemented.** `scan_state` exists and `quarantined` is wired end to end, but nothing populates it from a scanner — there is no scanner in the image. What *is* implemented is a meaningful subset of the quarantine behaviour: a file that does not decode is quarantined, never served. **A file that decodes cleanly is `clean` without ever having been scanned.** | — |

### Location and metadata

| Threat | Control | Where |
|---|---|---|
| GPS EXIF leaking the uploader's home or a hidden camera position | The derivative is built from **decoded pixels only**, and written with no `exif=` argument. There is no metadata to strip because none is ever carried across. | `build_derivative` |
| Original readable by anyone holding the report id | **No route serves the original.** The only byte-serving route returns the derivative and is reviewer-gated. | `api/routes/evidence.py` |
| Storage location guessable from a report id | Keys are `uuid4().hex`, minted by the server. A caller never contributes a path component, so `../../etc/passwd` as a filename is inert. | `services/media_storage.new_media_key` |
| A filename reaching the filesystem | Never. Filenames are stored as a display label, stripped of control characters and path segments, and are never used to build a path. | `_sanitize_label` |
| Reviewer learning the uploader's device | The filename is not echoed in any response, including the reviewer-gated one. | `EvidenceOut` |

### Authorisation and enumeration

| Threat | Control | Where |
|---|---|---|
| Anyone reading evidence bytes | `X-Reviewer-Key`, delegating to F1's `require_reviewer` so "who is a reviewer" has one answer. Unconfigured is 503, wrong key is 403. | `_require_reviewer` |
| Reading another report's evidence | `get()` requires the row's `report_id` to match the path's. | `EvidenceService.get` |
| Attaching to a report that does not exist | Refused before a single byte is read, so a bad id costs nothing and returns 404 rather than 500. | `EvidenceService.attach` |
| Reviewer key brute force | Out of scope: it is F1's shared-secret decision, not F2's. F2 inherits whatever F1 provides. | — |
| Seeing storage keys or filenames | No response schema contains either. | `EvidenceOut` |

### Resource exhaustion

| Threat | Control | Where |
|---|---|---|
| Disk fill / request size | 4 MiB per file, **3 per report**, plus a retention sweep. | `citizen_media_*` settings, `purge_expired` |
| Memory exhaustion per request | The body is read once, held once, and the size check precedes any decode. | `attach` |
| Upload flood | **Not implemented for evidence.** The per-/24 cap from F1 covers report *submission*, not photo upload; a caller who cannot submit reports can still try to attach. Bounded only by the 3-per-report cap per report id, which is not a global limit. **Known gap.** | — |
| CPU via repeated decode | The per-report cap bounds decodes per report to three. | `attach` |

### Orphaned files

The most likely operational failure, and the hardest to notice: bytes exist on
disk that no row points at, and a retention job driven *from rows* can never see
them. Two defences: any failure after the original is written removes it before
re-raising, and the filesystem store writes to a temp name and atomically
renames, so a crash never leaves a half object that reads as a valid photo.

The residual risk is a crash between `put` and the row insert. That window is
real and F2 does **not** close it — there is no reconciliation pass. **Known
gap.** The scheduled GitHub Actions workflow runs the retention sweep when
`CITIZEN_MEDIA_STORAGE=s3` is configured; keep its S3 credentials aligned with
the API deployment.

## What a reviewer actually sees, and what they must not conclude

`scan_state='clean'` means "decoded and re-encoded". It does **not** mean
"scanned", because nothing scans. `review_state='pending'` means no reviewer
has looked. Neither state qualifies the report: `report_lifecycle` never reads
evidence, and `tests/test_evidence.py` asserts that, because a report with no
photo is a first-class claim and letting a photo gate qualification would make
evidence a precondition rather than support.

## Accepted residual risks

1. **No malware scanning.** A decoder-level or library-level payload that
   decodes as an image is stored and can be shown to a reviewer.
2. **No reconciliation for orphans** after a crash in the write/link window.
3. **No upload rate limit** independent of the report-submission cap.
4. **Filesystem store is single-node.** Right for one persistent container,
   wrong for a serverless or scaled-out deployment. Use the S3-compatible store
   there and keep its bucket private.
5. **A polyglot that decodes** is shown to a reviewer as an image. The byte
   checks raise the cost; they do not make it impossible.
