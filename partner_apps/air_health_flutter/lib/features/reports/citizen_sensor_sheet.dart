import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/data_providers.dart';
import '../../providers/location_providers.dart';

/// Captures one manual PM2.5 reading from an external consumer sensor.
class CitizenSensorSheet extends ConsumerStatefulWidget {
  const CitizenSensorSheet({super.key});

  @override
  ConsumerState<CitizenSensorSheet> createState() => _CitizenSensorSheetState();
}

class _CitizenSensorSheetState extends ConsumerState<CitizenSensorSheet> {
  final _valueController = TextEditingController();
  final _deviceController = TextEditingController();
  bool _consent = false;
  bool _submitting = false;
  String? _submissionId;
  DateTime? _measuredAt;
  double? _pendingValue;
  String? _pendingDevice;
  double? _pendingLatitude;
  double? _pendingLongitude;

  @override
  void dispose() {
    _valueController.dispose();
    _deviceController.dispose();
    super.dispose();
  }

  Future<void> _refreshLocation() async {
    ref.invalidate(currentLocationProvider);
    await ref.read(currentLocationProvider.future);
  }

  Future<void> _submit() async {
    final api = ref.read(citizenSensorApiClientProvider);
    if (api == null || _submitting) return;
    final value = double.tryParse(_valueController.text.trim());
    final device = _deviceController.text.trim();
    if (value == null || value < 0 || value > 2000) {
      _message('Enter a PM2.5 value between 0 and 2000 µg/m³.');
      return;
    }
    if (device.isEmpty) {
      _message('Enter the sensor make or model.');
      return;
    }
    if (!_consent) {
      _message('Consent is required to share the reading and its location.');
      return;
    }
    final location = ref.read(resolvedLocationProvider);
    _submissionId ??= 'flutter-sensor-${DateTime.now().microsecondsSinceEpoch}';
    _measuredAt ??= DateTime.now().toUtc();
    _pendingValue ??= value;
    _pendingDevice ??= device;
    _pendingLatitude ??= location.latitude;
    _pendingLongitude ??= location.longitude;
    setState(() => _submitting = true);
    try {
      await api.submit(
        submissionId: _submissionId!,
        latitude: _pendingLatitude!,
        longitude: _pendingLongitude!,
        pm25UgM3: _pendingValue!,
        deviceLabel: _pendingDevice!,
        measuredAt: _measuredAt!,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (_) {
      if (!mounted) return;
      setState(() => _submitting = false);
      _message('Could not send the reading. Check your connection and try again.');
    }
  }

  void _message(String value) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(value)));
  }

  @override
  Widget build(BuildContext context) {
    final location = ref.watch(resolvedLocationProvider);
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        20,
        20,
        MediaQuery.of(context).viewInsets.bottom + 28,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Share a sensor reading', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              'Enter the PM2.5 value shown on your external air-quality sensor. '
              'A phone does not measure PM2.5.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: colors.onSurfaceVariant,
                  ),
            ),
            const SizedBox(height: 18),
            TextField(
              controller: _valueController,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(
                labelText: 'PM2.5 reading',
                suffixText: 'µg/m³',
                border: OutlineInputBorder(),
              ),
              readOnly: _submissionId != null,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _deviceController,
              maxLength: 80,
              readOnly: _submissionId != null,
              decoration: const InputDecoration(
                labelText: 'Sensor make or model',
                hintText: 'e.g. PurpleAir PA-II',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.location_on_outlined),
              title: Text(location.label ?? 'Current location'),
              subtitle: Text(
                '${location.latitude.toStringAsFixed(4)}, ${location.longitude.toStringAsFixed(4)}',
              ),
              trailing: IconButton(
                tooltip: 'Refresh location',
                onPressed: _submitting || _submissionId != null ? null : _refreshLocation,
                icon: const Icon(Icons.refresh),
              ),
            ),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: _consent,
              onChanged: _submitting || _submissionId != null
                  ? null
                  : (value) => setState(() => _consent = value ?? false),
              controlAffinity: ListTileControlAffinity.leading,
              title: const Text('Share this reading and precise location for authority review.'),
            ),
            Text(
              'This reading is labeled as citizen-submitted and does not currently affect forecasts. '
              'An authority can review and label the submission.',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: colors.onSurfaceVariant,
                  ),
            ),
            const SizedBox(height: 16),
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
                    : const Icon(Icons.sensors_outlined),
                label: Text(
                  _submitting
                      ? 'Sending…'
                      : _submissionId != null
                          ? 'Retry submission'
                          : 'Submit for review',
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
