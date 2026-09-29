import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../domain/models/fire_report.dart';
import '../../providers/data_providers.dart';
import '../../providers/location_providers.dart';
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
  FireKind _kind = FireKind.other;
  int _smokeIntensity = 3;
  double _durationHours = FireDurationOption.justStarted.hours;
  final _notesController = TextEditingController();
  final _regionController = TextEditingController();
  final _imagePicker = ImagePicker();
  Uint8List? _photoBytes;
  String? _photoFilename;
  bool _photoConsent = false;
  FireReport? _submittedReport;
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    final location = ref.read(resolvedLocationProvider);
    _regionController.text = location.label ?? 'Lucknow (LKO)';
  }

  @override
  void dispose() {
    _regionController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  /// Idempotency id for this draft session: a retried submission carries the
  /// same id, so the backend's unique constraint keeps retries from stacking.
  late final String _clientReportId =
      'flutter-${DateTime.now().microsecondsSinceEpoch}';

  Future<void> _refreshLocation() async {
    ref.invalidate(currentLocationProvider);
    // Kick the GPS now so the row below reflects it as soon as it resolves.
    final loc = await ref.read(currentLocationProvider.future);
    if (loc.label != null && _regionController.text.isEmpty) {
      _regionController.text = loc.label!;
    }
  }

  Future<void> _submit() async {
    if (_submitting) return;

    if (_photoBytes != null && !_photoConsent) {
      _showMessage('Please consent before attaching the photo.');
      return;
    }

    final location = ref.read(resolvedLocationProvider);
    final regionText = _regionController.text.trim().isNotEmpty
        ? _regionController.text.trim()
        : (location.label ?? 'Lucknow (LKO)');

    FireReportDraft? draft;
    if (_submittedReport == null) {
      try {
        draft = FireReportDraft.create(
          latitude: location.latitude,
          longitude: location.longitude,
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
        report = _submittedReport ?? await client.submitReport(draft!);
        _submittedReport ??= report;
        if (_photoBytes != null) {
          if (client is! CitizenPhotoApiClient) {
            throw StateError('Photo upload is unavailable for this API client.');
          }
          await (client as CitizenPhotoApiClient).attachPhoto(
            reportId: report.id,
            bytes: _photoBytes!,
            filename: _photoFilename ?? 'citizen-photo.jpg',
          );
        }
      } else {
        // Standalone on-device report when remote API is not configured
        report = FireReport(
          id: DateTime.now().millisecondsSinceEpoch ~/ 1000,
          h3Cell: 'citizen_cell',
          latitude: location.latitude,
          longitude: location.longitude,
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
      }

      // Record to local citizen reports store
      await ref.read(citizenReportsProvider.notifier).addReport(report);

      if (!mounted) return;
      Navigator.of(context).pop(report);
    } catch (_) {
      if (!mounted) return;
      setState(() => _submitting = false);
      _showMessage(_submittedReport == null
          ? 'Could not send the report — check your connection and try again.'
          : 'Your report was saved, but the photo could not be uploaded. Retry or remove the photo.');
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
      final bytes = await photo.readAsBytes();
      if (!mounted) return;
      setState(() {
        _photoBytes = bytes;
        _photoFilename = photo.name;
        _photoConsent = false;
      });
    } catch (_) {
      _showMessage('Could not open the photo picker. Check app permissions and try again.');
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
            Text('Report a fire',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Tell us about smoke or burning near you. The model treats it '
              'as an active source at this spot and picks it up on its next '
              'update cycle.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SectionLabel('Where is it happening?'),
            const SizedBox(height: AppSpacing.xs),
            _LocationRow(
              label: location.label,
              latitude: location.latitude,
              longitude: location.longitude,
              onRefresh: _refreshLocation,
            ),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _regionController,
              decoration: const InputDecoration(
                labelText: 'City / Region name',
                hintText: 'e.g. Lucknow, LKO, Hazratganj',
                prefixIcon: Icon(Icons.location_city_outlined),
              ),
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
            _SectionLabel('Add a photo (optional)'),
            const SizedBox(height: AppSpacing.xs),
            Text(
              'A photo can help responders assess smoke or burning. Photos are stored privately; '
              'reviewers see a metadata-stripped copy.',
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
                    label: const Text('Take photo'),
                  ),
                  OutlinedButton.icon(
                    onPressed: _submitting ? null : () => _pickPhoto(ImageSource.gallery),
                    icon: const Icon(Icons.photo_library_outlined),
                    label: const Text('Choose photo'),
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
                  errorBuilder: (_, __, ___) => const SizedBox(
                    height: 80,
                    child: Center(child: Text('Photo selected')),
                  ),
                ),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: _submitting ? null : _removePhoto,
                  icon: const Icon(Icons.delete_outline),
                  label: const Text('Remove photo'),
                ),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _photoConsent,
                onChanged: _submitting
                    ? null
                    : (value) => setState(() => _photoConsent = value ?? false),
                title: const Text('I consent to storing this photo with my report.'),
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
                      ? 'Sending…'
                      : _submittedReport != null && _photoBytes != null
                          ? 'Retry photo upload'
                          : 'Submit report',
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
