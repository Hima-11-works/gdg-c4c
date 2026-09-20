import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../domain/models/models.dart';
import '../notifications/scheduled_alarm.dart';

/// Persists the set of scheduled forecast alarms.
///
/// The OS holds the alarms themselves (they fire with the app killed); this
/// store only remembers *what* was scheduled, so a later reconciliation can
/// cancel moved/stale alarms, Settings can show what's upcoming, and a fresh
/// process can tell "already scheduled" from "needs scheduling".
///
/// Defensive by design: a keystore failure (reinstall, restored device, OS
/// upgrade) reads as "nothing scheduled", which only means the next
/// reconciliation re-schedules — the OS alarm for the old entry is either
/// still pending (and fires once) or was also lost.
class ForecastAlarmStore {
  ForecastAlarmStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  static const _key = 'forecast_alarms';

  Future<List<ScheduledAlarm>> readAll() async {
    String? raw;
    try {
      raw = await _storage.read(key: _key);
    } catch (_) {
      return const [];
    }
    if (raw == null) return const [];
    try {
      final list = jsonDecode(raw) as List<dynamic>;
      final alarms = <ScheduledAlarm>[];
      for (final item in list) {
        final alarm = _fromJson(item);
        if (alarm != null) alarms.add(alarm);
      }
      return alarms;
    } catch (_) {
      return const [];
    }
  }

  Future<void> writeAll(List<ScheduledAlarm> alarms) async {
    try {
      await _storage.write(
        key: _key,
        value: jsonEncode([for (final a in alarms) _toJson(a)]),
      );
    } catch (_) {
      // The alarms themselves are already registered with the OS; losing the
      // metadata only costs cancel/replace precision until the next save.
    }
  }

  Future<void> deleteAll() async {
    try {
      await _storage.delete(key: _key);
    } catch (_) {
      // Best effort — see readAll's note.
    }
  }

  static ScheduledAlarm? _fromJson(Object? raw) {
    if (raw is! Map) return null;
    final map = raw.cast<String, dynamic>();
    final fireAt = DateTime.tryParse(map['fireAt'] as String? ?? '');
    final id = (map['id'] as num?)?.toInt();
    final key = map['key'] as String?;
    final severityName = map['severity'] as String?;
    if (fireAt == null || id == null || key == null || severityName == null) {
      return null;
    }
    return ScheduledAlarm(
      id: id,
      key: key,
      fireAt: fireAt,
      severity: AlertSeverity.values.firstWhere(
        (s) => s.name == severityName,
        orElse: () => AlertSeverity.advisory,
      ),
      categoryLabel: (map['categoryLabel'] as String?) ?? '',
      predictedAqi: (map['predictedAqi'] as num?)?.toInt() ?? 0,
    );
  }

  static Map<String, dynamic> _toJson(ScheduledAlarm a) => <String, dynamic>{
        'id': a.id,
        'key': a.key,
        'fireAt': a.fireAt.toIso8601String(),
        'severity': a.severity.name,
        'categoryLabel': a.categoryLabel,
        'predictedAqi': a.predictedAqi,
      };
}
