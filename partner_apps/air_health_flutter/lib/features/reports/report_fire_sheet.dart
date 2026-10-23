import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/citizen_copy.dart';
import '../../domain/models/fire_report.dart';
import '../../providers/alert_providers.dart';
import '../../providers/data_providers.dart';
import '../../providers/location_providers.dart';
import '../../providers/prefs_providers.dart';
import '../../services/location_service.dart';
import '../../services/voice_input.dart';
import '../../storage/citizen_reports_store.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';

/// Citizen report of an active fire / air-polluting hotspot.
///
/// Presented as a modal bottom sheet from Home. On submit the
/// draft is validated client-side, sent to the backend if configured,
/// and persistently stored locally with citizen photograph evidence.
class ReportFireSheet extends ConsumerStatefulWidget {
  const ReportFireSheet({super.key});

  @override
  ConsumerState<ReportFireSheet> createState() => _ReportFireSheetState();
}

class _ReportFireSheetState extends ConsumerState<ReportFireSheet> {
  static const _maxPhotoBytes = 4 * 1024 * 1024;
  FireKind _kind = FireKind.other;
  int _smokeIntensity = 3;
  double _durationHours = FireDurationOption.justStarted.hours;
  final _notesController = TextEditingController();
  final _regionController = TextEditingController();
  final _latitudeController = TextEditingController();
  final _longitudeController = TextEditingController();
  final _imagePicker = ImagePicker();
  Uint8List? _photoBytes;
  String? _photoFilename;
  bool _photoConsent = false;
  FireReport? _submittedReport;
  bool _submitting = false;
  bool _voiceLoading = false;

  CitizenCopy get _copy => CitizenCopy(ref.read(appLanguageProvider));

  @override
  void initState() {
    super.initState();
    final location = ref.read(resolvedLocationProvider);
    if (!location.isFallback && location.label != null) {
      _regionController.text = location.label!;
    }
  }

  @override
  void dispose() {
    _regionController.dispose();
    _notesController.dispose();
    _latitudeController.dispose();
    _longitudeController.dispose();
    super.dispose();
  }

  /// Idempotency id for this draft session: a retried submission carries the
  /// same id, so the backend's unique constraint keeps retries from stacking.
  late final String _clientReportId =
      'flutter-${DateTime.now().microsecondsSinceEpoch}';

  Future<void> _refreshLocation() async {
    late final LocationResult result;
    try {
      result = await ref.read(locationServiceProvider).requestAndLocate();
    } catch (_) {
      _showMessage(_copy.text('permission_error'));
      return;
    }
    if (!mounted) return;
    if (result is LocationSuccess) {
      if (result.location.label != null) {
        _regionController.text = result.location.label!;
      }
      try {
        await ref.read(alertCoordinatorProvider).refreshAndEvaluate();
      } catch (_) {}
    } else {
      _showMessage(switch (result) {
        LocationDenied() => _copy.text('location_denied'),
        LocationPermanentlyDenied() => _copy.text('location_blocked'),
        _ => _copy.text('location_error'),
      });
    }
  }

  Future<void> _addVoiceNote() async {
    if (_voiceLoading) return;
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) {
      _showMessage(_copy.text('voice_unavailable'));
      return;
    }
    setState(() => _voiceLoading = true);
    try {
      final permission = await Permission.microphone.request();
      if (!permission.isGranted) {
        _showMessage(_copy.text('voice_permission'));
        return;
      }
      final localeId = switch (ref.read(appLanguageProvider)) {
        'hi' => 'hi-IN',
        'pa' => 'pa-IN',
        _ => 'en-IN',
      };
      final transcript = await const VoiceInputClient().recognize(localeId: localeId);
      if (!mounted || transcript == null || transcript.trim().isEmpty) return;
      final spoken = transcript.trim();
      final existing = _notesController.text.trim();
      final combined = existing.isEmpty ? spoken : '$existing $spoken';
      _notesController.value = TextEditingValue(
        text: combined,
        selection: TextSelection.collapsed(offset: combined.length),
      );
    } on PlatformException catch (error) {
      _showMessage(error.code == 'unavailable'
          ? _copy.text('voice_unavailable')
          : _copy.text('voice_failed'));
    } catch (_) {
      _showMessage(_copy.text('voice_failed'));
    } finally {
      if (mounted) setState(() => _voiceLoading = false);
    }
  }

  Future<void> _submit() async {
    if (_submitting) return;

    if (_photoBytes != null && !_photoConsent) {
      _showMessage(_copy.text('photo_consent_required'));
      return;
    }

    final location = ref.read(resolvedLocationProvider);
    if (location.isFallback) {
      final latitude = double.tryParse(_latitudeController.text.trim());
      final longitude = double.tryParse(_longitudeController.text.trim());
      if (latitude == null || longitude == null) {
        _showMessage(_copy.text('manual_coordinates_required'));
        return;
      }
      if (!latitude.isFinite || !longitude.isFinite ||
          latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
        _showMessage(_copy.text('manual_coordinates_invalid'));
        return;
      }
    }
    final reportLatitude = location.isFallback
        ? double.parse(_latitudeController.text.trim())
        : location.latitude;
    final reportLongitude = location.isFallback
        ? double.parse(_longitudeController.text.trim())
        : location.longitude;
    final regionText = _regionController.text.trim().isNotEmpty
        ? _regionController.text.trim()
        : location.isFallback
            ? _copy.text('manual_region_default')
            : (location.label ?? 'Nearby area');

    FireReportDraft? draft;
    if (_submittedReport == null) {
      try {
        draft = FireReportDraft.create(
          latitude: reportLatitude,
          longitude: reportLongitude,
          kind: _kind,
          smokeIntensity: _smokeIntensity,
          durationHours: _durationHours,
          region: regionText,
          photoBytes: _photoBytes,
          photoFilename: _photoFilename,
          notes: _notesController.text,
          clientReportId: _clientReportId,
        );
      } catch (error) {
        _showMessage('$error');
        return;
      }
    }

    HapticFeedback.mediumImpact();
    setState(() => _submitting = true);
    final client = ref.read(fireReportApiClientProvider);

    try {
      FireReport report;
      if (client != null) {
        final receipt = _submittedReport ?? await client.submitReport(draft!);
        _submittedReport ??= receipt;
        report = receipt.copyWith(
          region: regionText,
          photoBytes: _photoBytes,
          photoFilename: _photoFilename,
          clearPhoto: _photoBytes == null,
          photoUploadPending: _photoBytes != null,
        );
        // Persist the successful server receipt before uploading optional
        // evidence. The report can then be retried or have its photo removed
        // after this sheet closes or the app restarts.
        await ref.read(citizenReportsProvider.notifier).addReport(report);
        if (_photoBytes != null) {
          if (client is! CitizenPhotoApiClient) {
            throw StateError('Photo upload is unavailable for this API client.');
          }
          await (client as CitizenPhotoApiClient).attachPhoto(
            reportId: report.id,
            bytes: _photoBytes!,
            filename: _photoFilename ?? 'citizen-photo.jpg',
          );
          report = report.copyWith(photoUploadPending: false);
          await ref.read(citizenReportsProvider.notifier).addReport(report);
        }
      } else {
        // Standalone on-device report when remote API is not configured
        report = FireReport(
          id: DateTime.now().millisecondsSinceEpoch ~/ 1000,
          h3Cell: 'citizen_cell',
          latitude: reportLatitude,
          longitude: reportLongitude,
          kind: _kind,
          smokeIntensity: _smokeIntensity,
          durationHours: _durationHours,
          reportedAt: DateTime.now(),
          region: regionText,
          photoBytes: _photoBytes,
          photoFilename: _photoFilename,
          notes: _notesController.text.isEmpty ? null : _notesController.text,
          clientReportId: _clientReportId,
        );
        await ref.read(citizenReportsProvider.notifier).addReport(report);
      }

      if (!mounted) return;
      Navigator.of(context).pop(report);
    } catch (_) {
      if (!mounted) return;
      setState(() => _submitting = false);
      _showMessage(_submittedReport == null
          ? _copy.text('submit_failed')
          : _copy.text('photo_upload_failed'));
    }
  }

  Future<void> _pickPhoto(ImageSource source) async {
    try {
      final photo = await _imagePicker.pickImage(
        source: source,
        imageQuality: 85,
        maxWidth: 2048,
        maxHeight: 2048,
      );
      if (photo == null || !mounted) return;
      if (await photo.length() > _maxPhotoBytes) {
        _showMessage(_copy.text('photo_too_large'));
        return;
      }
      final bytes = await photo.readAsBytes();
      if (bytes.isEmpty) {
        _showMessage(_copy.text('photo_empty'));
        return;
      }
      final codec = await ui.instantiateImageCodec(bytes);
      try {
        final frame = await codec.getNextFrame();
        frame.image.dispose();
      } finally {
        codec.dispose();
      }
      if (!mounted) return;
      setState(() {
        _photoBytes = bytes;
        _photoFilename = photo.name;
        _photoConsent = false;
      });
    } catch (_) {
      _showMessage(_copy.text('photo_picker_failed'));
    }
  }

  void _removePhoto() {
    setState(() {
      _photoBytes = null;
      _photoFilename = null;
      _photoConsent = false;
    });
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final location = ref.watch(resolvedLocationProvider);
    final copy = CitizenCopy(ref.watch(appLanguageProvider));

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
            Text(copy.text('report_title'),
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.sm),
            Text(
              copy.text('report_help'),
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel(copy.text('where')),
            const SizedBox(height: AppSpacing.xs),
            _LocationRow(
              copy: copy,
              label: location.isFallback
                  ? '${copy.text('fallback')} · ${location.label ?? "Bhubaneswar"}'
                  : location.label,
              latitude: location.latitude,
              longitude: location.longitude,
              onRefresh: _refreshLocation,
            ),
            if (location.isFallback) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(copy.text('manual_location'),
                  style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant)),
              const SizedBox(height: AppSpacing.xs),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      key: const ValueKey('manual-latitude'),
                      controller: _latitudeController,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                      decoration: InputDecoration(labelText: copy.text('latitude')),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: TextField(
                      key: const ValueKey('manual-longitude'),
                      controller: _longitudeController,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                      decoration: InputDecoration(labelText: copy.text('longitude')),
                    ),
                  ),
                ],
              ),
            ],
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _regionController,
              decoration: InputDecoration(
                labelText: copy.text('region'),
                hintText: copy.text('region_hint'),
                prefixIcon: Icon(Icons.location_city_outlined),
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel(copy.text('what_burning')),
            const SizedBox(height: AppSpacing.xs),
            _KindChips(
              selected: _kind,
              copy: copy,
              onSelected: (kind) => setState(() => _kind = kind),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel(copy.text('smoke_amount')),
            const SizedBox(height: AppSpacing.xs),
            _SmokeSlider(
              value: _smokeIntensity,
              copy: copy,
              onChanged: (value) => setState(() => _smokeIntensity = value),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel(copy.text('duration_question')),
            const SizedBox(height: AppSpacing.xs),
            _DurationChips(
              selectedHours: _durationHours,
              copy: copy,
              onSelected: (hours) => setState(() => _durationHours = hours),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel(copy.text('other_optional')),
            const SizedBox(height: AppSpacing.xs),
            TextField(
              controller: _notesController,
              maxLines: 2,
              maxLength: FireReportDraft.maxNotesLength,
              decoration: InputDecoration(
                hintText: copy.text('notes_hint'),
                counterText: '',
                suffixIcon: IconButton(
                  tooltip: copy.text('voice_input'),
                  onPressed: _submitting || _voiceLoading ? null : _addVoiceNote,
                  icon: _voiceLoading
                      ? const SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.mic_none),
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              copy.text('voice_privacy'),
              style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel(copy.text('photo_optional')),
            const SizedBox(height: AppSpacing.xs),
            Text(
              copy.text('photo_help'),
              style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.sm),
            if (_photoBytes == null)
              Wrap(
                spacing: AppSpacing.sm,
                children: [
                  OutlinedButton.icon(
                    onPressed: _submitting ? null : () => _pickPhoto(ImageSource.camera),
                    icon: const Icon(Icons.camera_alt_outlined),
                    label: Text(copy.text('take_photo')),
                  ),
                  OutlinedButton.icon(
                    onPressed: _submitting ? null : () => _pickPhoto(ImageSource.gallery),
                    icon: const Icon(Icons.photo_library_outlined),
                    label: Text(copy.text('choose_photo')),
                  ),
                ],
              )
            else ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.memory(
                  _photoBytes!,
                  height: 160,
                  width: double.infinity,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => SizedBox(
                    height: 80,
                    child: Center(child: Text(copy.text('photo_selected'))),
                  ),
                ),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: _submitting ? null : _removePhoto,
                  icon: const Icon(Icons.delete_outline),
                  label: Text(copy.text('remove_photo')),
                ),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _photoConsent,
                onChanged: _submitting
                    ? null
                    : (value) => setState(() => _photoConsent = value ?? false),
                title: Text(copy.text('photo_consent')),
                controlAffinity: ListTileControlAffinity.leading,
              ),
            ],
            const SizedBox(height: AppSpacing.xxl),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _submitting ? null : _submit,
                icon: _submitting
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send),
                label: Text(
                  _submitting
                      ? copy.text('sending')
                      : _submittedReport != null && _photoBytes != null
                          ? copy.text('retry_photo')
                          : copy.text('submit'),
                ),
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

class _LocationRow extends StatelessWidget {
  const _LocationRow({
    required this.copy,
    required this.label,
    required this.latitude,
    required this.longitude,
    required this.onRefresh,
  });

  final CitizenCopy copy;
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
                  label ?? copy.text('location_current'),
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
            tooltip: copy.text('use_location'),
            onPressed: onRefresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
    );
  }
}

class _KindChips extends StatelessWidget {
  const _KindChips({required this.selected, required this.copy, required this.onSelected});

  final FireKind selected;
  final CitizenCopy copy;
  final ValueChanged<FireKind> onSelected;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      children: [
        for (final kind in FireKind.values)
          ChoiceChip(
            label: Text(copy.fireKind(kind.value)),
            selected: kind == selected,
            onSelected: (_) => onSelected(kind),
          ),
      ],
    );
  }
}

class _SmokeSlider extends StatelessWidget {
  const _SmokeSlider({required this.value, required this.copy, required this.onChanged});

  final int value;
  final CitizenCopy copy;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Slider(
          value: value.toDouble(),
          min: FireReportDraft.minIntensity.toDouble(),
          max: FireReportDraft.maxIntensity.toDouble(),
          divisions: FireReportDraft.maxIntensity - FireReportDraft.minIntensity,
          label: copy.smoke(value),
          activeColor: _colorFor(context),
          onChanged: (v) => onChanged(v.round()),
        ),
        Text(
          copy.smoke(value),
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
  const _DurationChips({required this.selectedHours, required this.copy, required this.onSelected});

  final double selectedHours;
  final CitizenCopy copy;
  final ValueChanged<double> onSelected;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      children: [
        for (final option in FireDurationOption.all)
          ChoiceChip(
            label: Text(copy.duration(option.hours)),
            selected: option.hours == selectedHours,
            onSelected: (_) => onSelected(option.hours),
          ),
      ],
    );
  }
}
