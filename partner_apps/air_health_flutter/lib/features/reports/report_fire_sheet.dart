import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/reports/fire_report_api.dart';
import '../../domain/models/fire_report.dart';
import '../../domain/models/report_evidence.dart';
import '../../providers/data_providers.dart';
import '../../providers/location_providers.dart';
import '../../services/evidence_photo_picker.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';

/// Citizen report of an active fire / burning event, plus its evidence.
///
/// Two steps against two endpoints, and the split is the whole point:
///
///  1. `POST /api/v1/reports` creates the report, with a client-generated
///     idempotency id so a retry can never stack reports.
///  2. `POST /api/v1/reports/{id}/evidence` attaches the selected photo and/or
///     the resident's own sensor reading. It is keyed by the id step 1
///     returned, and that id is kept for the rest of the sheet's life, so a
///     failed evidence upload is retried against the report that already exists
///     rather than by filing a second one. The evidence call carries its own
///     idempotency key - the same one on every retry - which is what makes the
///     retry return the stored record instead of a second record.
///
/// The sheet stays open when the evidence fails, because closing it would
/// throw away the report id that makes the retry possible. It pops only when
/// there is nothing left to send.
class ReportFireSheet extends ConsumerStatefulWidget {
  const ReportFireSheet({super.key});

  @override
  ConsumerState<ReportFireSheet> createState() => _ReportFireSheetState();
}

class _ReportFireSheetState extends ConsumerState<ReportFireSheet> {
  FireKind _kind = FireKind.other;
  int _smokeIntensity = 3;
  double _durationHours = FireDurationOption.justStarted.hours;
  final _notesController = TextEditingController();
  final _readingController = TextEditingController();

  SensorPollutant _pollutant = SensorPollutant.pm25;
  String _readingUnit = 'µg/m³';
  bool _attachReading = false;
  DateTime _measuredAt = DateTime.now();

  bool _submitting = false;
  bool _failed = false;
  String? _validationError;

  /// The report once created. Non-null is what switches the sheet from
  /// "create a report" to "attach evidence to report N".
  FireReport? _report;
  ReportEvidence? _evidence;
  EvidenceUploadException? _evidenceFailure;
  bool _uploading = false;
  double? _uploadProgress;

  EvidencePhoto? _photo;
  bool _pickingPhoto = false;
  String? _photoError;

  @override
  void dispose() {
    _notesController.dispose();
    _readingController.dispose();
    super.dispose();
  }

  /// Idempotency id for this draft session: a retried submission carries the
  /// same id, so the backend's unique constraint keeps retries from stacking.
  ///
  /// Minted once per sheet (a field, not a getter) — a getter would hand out a
  /// fresh id on every attempt, which is exactly what makes a retry after a
  /// timeout duplicate the report instead of returning the stored one.
  late final String _clientReportId =
      'flutter-${DateTime.now().microsecondsSinceEpoch}';

  /// The evidence upload's own idempotency key, derived from the report's so
  /// one stable pair exists per sheet. Same value on every retry.
  String get _evidenceClientId => 'ev-$_clientReportId';

  /// The reading the resident typed, parsed against the backend's bounds.
  /// Ignores the opt-in checkbox, so the sheet can show what it would send.
  CitizenSensorEvidence? get _parsedReading => CitizenSensorEvidence.tryCreate(
        rawValue: _readingController.text,
        pollutant: _pollutant,
        unit: _readingUnit,
        measuredAt: _measuredAt,
      );

  bool get _hasReading => _parsedReading != null;

  /// The reading that will actually be attached, or null.
  CitizenSensorEvidence? get _reading => _attachReading ? _parsedReading : null;

  Future<void> _refreshLocation() async {
    ref.invalidate(currentLocationProvider);
    // Kick the GPS now so the row below reflects it as soon as it resolves.
    await ref.read(currentLocationProvider.future);
  }

  Future<void> _pickPhoto() async {
    if (_pickingPhoto) return;
    setState(() {
      _pickingPhoto = true;
      _photoError = null;
    });
    try {
      final picked = await ref.read(evidencePhotoPickerProvider).pickPhoto();
      if (!mounted) return;
      if (picked == null) {
        setState(() {});
        return;
      }
      // Checked here so an unusable file is refused before it is uploaded,
      // while the server stays the authority on the bytes themselves.
      if (!picked.typeIsAcceptable) {
        setState(() {
          _photoError =
              'The backend accepts ${EvidencePhoto.defaultAllowedTypes.join(', ')}.';
        });
        return;
      }
      if (!picked.isWithinSizeCap) {
        setState(() {
          _photoError =
              'That photo is ${_formatBytes(picked.byteSize)}; the cap is '
              '${_formatBytes(EvidencePhoto.defaultMaxBytes)}.';
        });
        return;
      }
      setState(() {
        _photo = picked;
        _photoError = null;
      });
    } on EvidencePhotoPickerException catch (error) {
      if (!mounted) return;
      setState(() {
        _photoError = error.message;
      });
    } finally {
      if (mounted) setState(() => _pickingPhoto = false);
    }
  }

  void _removePhoto() {
    setState(() {
      _photo = null;
      _photoError = null;
    });
  }

  Future<void> _submit() async {
    final client = ref.read(fireReportApiClientProvider);
    // Once the report exists, this method must never run again: it would file
    // a second report. Everything from here on goes through _uploadEvidence.
    if (client == null || _submitting || _report != null) return;

    final location = ref.read(resolvedLocationProvider);
    final FireReportDraft draft;
    try {
      draft = FireReportDraft.create(
        latitude: location.latitude,
        longitude: location.longitude,
        kind: _kind,
        smokeIntensity: _smokeIntensity,
        durationHours: _durationHours,
        notes: _notesController.text,
        clientReportId: _clientReportId,
      );
    } catch (error) {
      setState(() {
        _validationError = '$error';
        _failed = true;
      });
      _showMessage('$error');
      return;
    }

    HapticFeedback.mediumImpact();
    setState(() {
      _submitting = true;
      _failed = false;
      _validationError = null;
    });
    try {
      final report = await client.submitReport(draft);
      if (!mounted) return;
      setState(() => _report = report);
      // The report exists now. Everything below hangs off its id and cannot
      // create another report. With nothing attached there is no second call
      // to make, and no error to raise about it.
      if (_evidenceSelected) {
        await _uploadEvidence(report.id);
        if (!mounted) return;
        if (_evidence != null) Navigator.of(context).pop(_report);
      } else {
        Navigator.of(context).pop(_report);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _failed = true;
      });
      _showMessage(
        'Could not send the report — check your connection and try again.',
      );
    }
  }

  bool get _evidenceSelected => _photo != null || _reading != null;

  /// Send (or resend) the evidence for a report that already exists.
  Future<void> _uploadEvidence(int reportId) async {
    final client = ref.read(fireReportApiClientProvider);
    if (client == null || _uploading) return;

    final photo = _photo;
    final sensor = _reading;
    if (photo == null && sensor == null) {
      setState(() {
        _validationError =
            'Attach a photo, a sensor reading, or both — there is nothing to send.';
        _evidenceFailure = null;
      });
      return;
    }

    setState(() {
      _uploading = true;
      _uploadProgress = null;
      _evidenceFailure = null;
      _validationError = null;
    });
    try {
      final evidence = await client.submitEvidence(
        reportId: reportId,
        clientReportId: _evidenceClientId,
        photo: photo,
        sensor: sensor,
        notes: _notesController.text,
        onProgress: (sent, total) {
          if (!mounted) return;
          setState(() {
            _uploadProgress = total > 0 ? (sent / total).clamp(0.0, 1.0) : null;
          });
        },
      );
      if (!mounted) return;
      setState(() {
        _evidence = evidence;
        _submitting = false;
      });
    } on EvidenceUploadException catch (error) {
      if (!mounted) return;
      setState(() {
        _uploading = false;
        _evidenceFailure = error;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _uploading = false;
        _evidenceFailure = const EvidenceUploadException(
          code: 'network_error',
          message: 'The upload did not complete.',
        );
      });
    } finally {
      if (mounted) {
        setState(() {
          _uploading = false;
          _uploadProgress = null;
        });
      }
    }
  }

  /// The one primary action, which depends on how far the flow has got.
  ///
  /// Before the report exists it creates it. After that it can only ever
  /// resend evidence against the report already stored - the retry inside the
  /// failure panel is the same call, so there is no path from this screen that
  /// files a second report.
  void _primaryAction(FireReport? report) {
    if (report == null) {
      _submit();
      return;
    }
    if (_evidence != null || !_evidenceSelected) {
      // Nothing left to send: the report is stored and either the evidence
      // arrived or the resident removed what was left to attach.
      Navigator.of(context).pop(_report);
      return;
    }
    _uploadEvidence(report.id);
  }

  String _primaryLabel(FireReport? report) {
    if (_submitting) return 'Sending…';
    if (_uploading) return 'Uploading…';
    if (report == null) return 'Submit report';
    if (_evidence != null || !_evidenceSelected) return 'Done';
    final failure = _evidenceFailure;
    if (failure != null && !failure.isRetryable) {
      return 'Send it again (after changing it above)';
    }
    return 'Retry evidence upload';
  }

  IconData _primaryIcon(FireReport? report) {
    if (report == null) return Icons.send;
    if (_evidence != null || !_evidenceSelected) return Icons.check;
    return Icons.refresh;
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  static String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final report = _report;

    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(report == null ? 'Report a fire' : 'Report stored',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.sm),
            Text(
              report == null
                  ? 'Tell us about smoke or burning near you. The model treats it '
                      'as an active source at this spot and picks it up on its next '
                      'update cycle.'
                  : 'The backend stored report #${report.id} and snapped it to H3 '
                      'cell ${report.h3Cell}. The model picks it up on its next '
                      'update cycle — the map will not change immediately.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            if (report == null) ...[
              _SectionLabel('Where is it happening?'),
              const SizedBox(height: AppSpacing.xs),
              _LocationRow(
                label: ref.watch(resolvedLocationProvider).label,
                latitude: ref.watch(resolvedLocationProvider).latitude,
                longitude: ref.watch(resolvedLocationProvider).longitude,
                onRefresh: _refreshLocation,
              ),
              const SizedBox(height: AppSpacing.lg),
              _SectionLabel('What is burning?'),
              const SizedBox(height: AppSpacing.xs),
              _KindChips(
                selected: _kind,
                onSelected: (kind) => setState(() => _kind = kind),
              ),
              const SizedBox(height: AppSpacing.lg),
              _SectionLabel('How much smoke?'),
              const SizedBox(height: AppSpacing.xs),
              _SmokeSlider(
                value: _smokeIntensity,
                onChanged: (value) => setState(() => _smokeIntensity = value),
              ),
              const SizedBox(height: AppSpacing.lg),
              _SectionLabel('How long has it been going?'),
              const SizedBox(height: AppSpacing.xs),
              _DurationChips(
                selectedHours: _durationHours,
                onSelected: (hours) => setState(() => _durationHours = hours),
              ),
              const SizedBox(height: AppSpacing.lg),
              _SectionLabel('Anything else? (optional)'),
              const SizedBox(height: AppSpacing.xs),
            ],
            TextField(
              controller: _notesController,
              maxLines: 2,
              maxLength: FireReportDraft.maxNotesLength,
              decoration: const InputDecoration(
                hintText: 'e.g. crop stubble, smoke drifting toward the road',
                counterText: '',
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel('Photo (optional)'),
            const SizedBox(height: AppSpacing.xs),
            Text(
              CitizenReportVerification.evidenceUploadDetail,
              style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xs),
            _PhotoRow(
              photo: _photo,
              picking: _pickingPhoto,
              onPick: _pickPhoto,
              onRemove: _removePhoto,
            ),
            if (_photoError != null) ...[
              const SizedBox(height: AppSpacing.xs),
              _InlineError(_photoError!),
            ],
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel('Local sensor reading (optional)'),
            const SizedBox(height: AppSpacing.xs),
            Text(
              'From a monitor you own. Stored as unverified evidence with its '
              'unit and your timestamp — never as a station observation.',
              style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xs),
            _LocalReadingRow(
              controller: _readingController,
              unit: _readingUnit,
              pollutant: _pollutant,
              onPollutantChanged: (value) => setState(() => _pollutant = value),
              onUnitChanged: (unit) => setState(() => _readingUnit = unit),
              onChanged: () => setState(() {}),
            ),
            const SizedBox(height: AppSpacing.xs),
            CheckboxListTile(
              value: _attachReading,
              onChanged: _hasReading
                  ? (value) => setState(() => _attachReading = value ?? false)
                  : null,
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: Text(
                _parsedReading != null
                    ? 'Attach ${_parsedReading!.display} as evidence'
                    : 'Attach this reading (enter a value of 0 or more first)',
                style: AppTypography.bodySmall,
              ),
            ),
            if (_attachReading && _hasReading) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                'Reading taken at ${_measuredAt.toLocal()} — tap to set the time '
                'it was actually taken.',
                style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: AppSpacing.xs),
              OutlinedButton.icon(
                onPressed: () async {
                  final picked = await showDatePicker(
                    context: context,
                    initialDate: _measuredAt,
                    firstDate: DateTime.now().subtract(const Duration(days: 3)),
                    lastDate: DateTime.now(),
                  );
                  if (picked == null || !mounted) return;
                  setState(() => _measuredAt = DateTime(
                        picked.year,
                        picked.month,
                        picked.day,
                        _measuredAt.hour,
                        _measuredAt.minute,
                      ));
                },
                icon: const Icon(Icons.schedule, size: 18),
                label: const Text('Set the time measured'),
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            const _UnverifiedNotice(),
            if (_validationError != null) ...[
              const SizedBox(height: AppSpacing.sm),
              _InlineError(_validationError!),
            ],
            if (_uploading) ...[
              const SizedBox(height: AppSpacing.sm),
              _UploadProgress(progress: _uploadProgress, reportId: report?.id),
            ],
            if (_evidenceFailure != null && report != null) ...[
              const SizedBox(height: AppSpacing.sm),
              _EvidenceFailurePanel(
                failure: _evidenceFailure!,
                reportId: report.id,
                onRetry: () => _uploadEvidence(report.id),
              ),
            ],
            if (_evidence != null) ...[
              const SizedBox(height: AppSpacing.sm),
              _StoredEvidencePanel(evidence: _evidence!),
            ],
            if (_failed && report == null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                'Retrying reuses the same report id, so it cannot create a '
                'duplicate — if the first attempt did reach the server, this '
                'returns the report it already stored.',
                style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
              ),
            ],
            const SizedBox(height: AppSpacing.xxl),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _submitting || _uploading ? null : _primaryAction(report),
                icon: _submitting || _uploading
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Icon(_primaryIcon(report)),
                label: Text(_primaryLabel(report)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Text(
      text,
      style: AppTypography.titleMedium.copyWith(color: cs.onSurface),
    );
  }
}

/// The selected photo, or the control that selects one.
class _PhotoRow extends StatelessWidget {
  const _PhotoRow({
    required this.photo,
    required this.picking,
    required this.onPick,
    required this.onRemove,
  });

  final EvidencePhoto? photo;
  final bool picking;
  final VoidCallback onPick;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final selected = photo;
    if (selected == null) {
      return OutlinedButton.icon(
        onPressed: picking ? null : onPick,
        icon: picking
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.photo_camera_outlined, size: 18),
        label: Text(picking ? 'Opening…' : 'Choose a photo'),
      );
    }
    return Row(
      children: [
        const Icon(Icons.image_outlined, size: 18),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(
            '${selected.name} · ${selected.contentType} · '
            '${(selected.byteSize / 1024).toStringAsFixed(0)} KB',
            style: AppTypography.bodySmall,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        TextButton(onPressed: onRemove, child: const Text('Remove')),
      ],
    );
  }
}

/// A resident's own monitor reading, with the pollutant and unit the backend
/// requires. All four parts travel together or not at all.
class _LocalReadingRow extends StatelessWidget {
  const _LocalReadingRow({
    required this.controller,
    required this.unit,
    required this.pollutant,
    required this.onUnitChanged,
    required this.onPollutantChanged,
    required this.onChanged,
  });

  final TextEditingController controller;
  final String unit;
  final SensorPollutant pollutant;
  final ValueChanged<String> onUnitChanged;
  final ValueChanged<SensorPollutant> onPollutantChanged;

  /// Fired as the resident types, so the attach checkbox can enable itself.
  final VoidCallback onChanged;

  static const units = <String>['µg/m³', 'ppm', 'AQI', 'ppb', 'other'];

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        DropdownButton<SensorPollutant>(
          value: pollutant,
          onChanged: (value) {
            if (value != null) onPollutantChanged(value);
          },
          items: [
            for (final option in SensorPollutant.values)
              DropdownMenuItem<SensorPollutant>(
                value: option,
                child: Text(option.label),
              ),
          ],
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: TextField(
            key: const Key('report-reading-value'),
            controller: controller,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            onChanged: (_) => onChanged(),
            decoration: const InputDecoration(
              hintText: 'e.g. 87.5',
              isDense: true,
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        DropdownButton<String>(
          value: units.contains(unit) ? unit : units.first,
          onChanged: (value) {
            if (value != null) onUnitChanged(value);
          },
          items: [
            for (final option in units)
              DropdownMenuItem<String>(value: option, child: Text(option)),
          ],
        ),
      ],
    );
  }
}

/// Upload progress. A null fraction means the transport does not know the
/// total yet, which is shown as indeterminate rather than as a fake number.
class _UploadProgress extends StatelessWidget {
  const _UploadProgress({required this.progress, required this.reportId});

  final double? progress;
  final int? reportId;

  @override
  Widget build(BuildContext context) {
    final target = reportId == null ? 'the report' : 'report #$reportId';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          progress == null
              ? 'Uploading evidence to $target…'
              : 'Uploading evidence to $target… '
                  '${(progress! * 100).round()}%',
          style: AppTypography.bodySmall,
        ),
        const SizedBox(height: AppSpacing.xs),
        LinearProgressIndicator(value: progress),
      ],
    );
  }
}

/// Why the evidence was not stored, and the one button that can help.
class _EvidenceFailurePanel extends StatelessWidget {
  const _EvidenceFailurePanel({
    required this.failure,
    required this.reportId,
    required this.onRetry,
  });

  final EvidenceUploadException failure;
  final int reportId;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final status = failure.statusCode;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: cs.errorContainer.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: cs.error.withValues(alpha: 0.5)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Evidence was not stored (${failure.code}'
            '${status == null ? '' : ', HTTP $status'}).',
            style: AppTypography.bodySmall.copyWith(color: cs.error),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(failure.message, style: AppTypography.bodySmall),
          const SizedBox(height: AppSpacing.xs),
          Text(failure.explanation, style: AppTypography.bodySmall),
          const SizedBox(height: AppSpacing.xs),
          Text(
            'Report #$reportId is already stored. Retrying uploads the evidence '
            'to that same report — it cannot create a second one.',
            style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
          ),
          const SizedBox(height: AppSpacing.sm),
          Align(
            alignment: Alignment.centerLeft,
            child: FilledButton.tonalIcon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh, size: 18),
              label: Text(
                failure.isRetryable
                    ? 'Retry evidence upload'
                    : 'Send it again (after changing it above)',
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// What the server actually stored, and the status it reported.
class _StoredEvidencePanel extends StatelessWidget {
  const _StoredEvidencePanel({required this.evidence});

  final ReportEvidence evidence;

  /// A digest shorter than the requested length is shown whole rather than
  /// sliced, so a malformed value cannot crash the panel.
  static String _shortDigest(String sha256) {
    final trimmed = sha256.length <= 12 ? sha256 : sha256.substring(0, 12);
    return trimmed == sha256 ? trimmed : '$trimmed…';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final status = evidence.verificationStatus;
    final media = evidence.media;
    final sensor = evidence.sensor;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.aqiGood.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.aqiGood.withValues(alpha: 0.4)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Evidence stored on the server',
              style: AppTypography.titleSmall.copyWith(color: cs.onSurface)),
          const SizedBox(height: AppSpacing.xs),
          if (media != null)
            Text(
              'Photo: ${media.contentType} · '
              '${(media.byteSize / 1024).toStringAsFixed(0)} KB · '
              'sha256 ${_shortDigest(media.sha256)}',
              style: AppTypography.bodySmall,
            ),
          if (sensor != null)
            Text(
              'Local reading: ${sensor.value} ${sensor.unit} ${sensor.pollutant} at '
              '${sensor.measuredAt.toLocal()} · source ${sensor.source} · '
              'verified ${sensor.verified}',
              style: AppTypography.bodySmall,
            ),
          if (media == null && sensor == null)
            Text('No photo or reading was attached.',
                style: AppTypography.bodySmall),
          const SizedBox(height: AppSpacing.xs),
          Text(
            '${status.label} — ${status.detail}',
            style: AppTypography.bodySmall.copyWith(
              color: status == EvidenceVerificationStatus.verified
                  ? AppColors.aqiGood
                  : cs.onSurface,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(kEvidenceNeverAMeasurement,
              style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant)),
          const SizedBox(height: AppSpacing.xs),
          Text(
            'Stored ${evidence.submittedAt.toLocal()} · evidence #${evidence.id}',
            style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

/// Says out loud what a resident submission is: unverified. Shown on the form
/// itself, before submission, so the resident knows how their report will be
/// treated rather than discovering it afterwards.
class _UnverifiedNotice extends StatelessWidget {
  const _UnverifiedNotice();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.aqiModerate.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.info_outline, size: 18),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              '${CitizenReportVerification.badge}. ${CitizenReportVerification.tooltip}',
              style: AppTypography.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}

/// Inline validation failure — the same bounds the backend enforces, caught
/// before the request so the user gets a message at the form instead of a 422.
class _InlineError extends StatelessWidget {
  const _InlineError(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return Text(
      message,
      style: AppTypography.bodySmall
          .copyWith(color: Theme.of(context).colorScheme.error),
    );
  }
}

class _LocationRow extends StatelessWidget {
  const _LocationRow({
    required this.label,
    required this.latitude,
    required this.longitude,
    required this.onRefresh,
  });

  final String? label;
  final double latitude;
  final double longitude;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: cs.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Icon(Icons.my_location, size: 18, color: cs.primary),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label ?? 'Current position',
                  style: AppTypography.titleMedium.copyWith(color: cs.onSurface),
                ),
                Text(
                  '${latitude.toStringAsFixed(4)}, ${longitude.toStringAsFixed(4)}',
                  style: AppTypography.bodySmall.copyWith(
                    color: cs.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          IconButton(
            tooltip: 'Use my location',
            onPressed: onRefresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
    );
  }
}

class _KindChips extends StatelessWidget {
  const _KindChips({required this.selected, required this.onSelected});

  final FireKind selected;
  final ValueChanged<FireKind> onSelected;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      children: [
        for (final kind in FireKind.values)
          ChoiceChip(
            label: Text(kind.label),
            selected: kind == selected,
            onSelected: (_) => onSelected(kind),
          ),
      ],
    );
  }
}

class _SmokeSlider extends StatelessWidget {
  const _SmokeSlider({required this.value, required this.onChanged});

  final int value;
  final ValueChanged<int> onChanged;

  static const _labels = ['Low', 'Moderate', 'High', 'Very high', 'Extreme'];

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Slider(
          value: value.toDouble(),
          min: FireReportDraft.minIntensity.toDouble(),
          max: FireReportDraft.maxIntensity.toDouble(),
          divisions: FireReportDraft.maxIntensity - FireReportDraft.minIntensity,
          label: _labels[value - 1],
          activeColor: _colorFor(context),
          onChanged: (v) => onChanged(v.round()),
        ),
        Text(
          _labels[value - 1],
          style: AppTypography.labelLarge.copyWith(color: _colorFor(context)),
        ),
      ],
    );
  }

  Color _colorFor(BuildContext context) {
    // Smoke amount borrows the AQI ramp only as an intensity cue.
    return switch (value) {
      1 => AppColors.aqiGood,
      2 => AppColors.aqiSatisfactory,
      3 => AppColors.aqiModerate,
      4 => AppColors.aqiPoor,
      _ => AppColors.aqiVeryPoor,
    };
  }
}

class _DurationChips extends StatelessWidget {
  const _DurationChips({required this.selectedHours, required this.onSelected});

  final double selectedHours;
  final ValueChanged<double> onSelected;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      children: [
        for (final option in FireDurationOption.all)
          ChoiceChip(
            label: Text(option.label),
            selected: option.hours == selectedHours,
            onSelected: (_) => onSelected(option.hours),
          ),
      ],
    );
  }
}
