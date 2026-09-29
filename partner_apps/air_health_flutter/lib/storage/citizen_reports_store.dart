import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../domain/models/fire_report.dart';

/// Local store that saves citizen pollution and fire hotspot reports (including
/// photographs and region metadata), ensuring data is preserved even when
/// offline or prior to backend sync.
class CitizenReportsStore {
  CitizenReportsStore({SharedPreferences? prefs}) : _prefs = prefs;

  SharedPreferences? _prefs;
  static const _keyReports = 'citizen_hotspot_reports_v1';
  final List<FireReport> _inMemory = [];

  Future<void> _ensureInitialized() async {
    if (_prefs != null) return;
    try {
      _prefs = await SharedPreferences.getInstance();
    } catch (_) {
      // Fallback to in-memory store in unit test or mock environments
    }
  }

  /// Saves a citizen report to local storage.
  Future<void> saveReport(FireReport report) async {
    await _ensureInitialized();
    _inMemory.removeWhere((r) => r.id == report.id);
    _inMemory.insert(0, report);
    await _persist();
  }

  /// Retrieves all citizen reports stored on this device.
  Future<List<FireReport>> getAllReports() async {
    await _ensureInitialized();
    if (_inMemory.isNotEmpty) return List.unmodifiable(_inMemory);

    if (_prefs == null) return List.unmodifiable(_inMemory);

    final raw = _prefs?.getStringList(_keyReports) ?? [];
    _inMemory.clear();
    for (final item in raw) {
      try {
        final map = jsonDecode(item) as Map<String, dynamic>;
        Uint8List? photoBytes;
        if (map['photoBase64'] != null) {
          photoBytes = base64Decode(map['photoBase64'] as String);
        }
        _inMemory.add(
          FireReport(
            id: (map['id'] as num).toInt(),
            h3Cell: map['h3Cell'] as String? ?? '',
            latitude: (map['latitude'] as num).toDouble(),
            longitude: (map['longitude'] as num).toDouble(),
            kind: FireKind.fromValue(map['kind'] as String),
            smokeIntensity: (map['smokeIntensity'] as num).toInt(),
            durationHours: (map['durationHours'] as num).toDouble(),
            reportedAt: DateTime.parse(map['reportedAt'] as String),
            region: map['region'] as String?,
            photoBytes: photoBytes,
            photoFilename: map['photoFilename'] as String?,
            notes: map['notes'] as String?,
            clientReportId: map['clientReportId'] as String?,
          ),
        );
      } catch (_) {}
    }
    return List.unmodifiable(_inMemory);
  }

  Future<void> _persist() async {
    if (_prefs == null) return;
    try {
      final encoded = _inMemory.map((r) {
        String? photoBase64;
        if (r.photoBytes != null && r.photoBytes!.isNotEmpty) {
          photoBase64 = base64Encode(r.photoBytes!);
        }
        return jsonEncode({
          'id': r.id,
          'h3Cell': r.h3Cell,
          'latitude': r.latitude,
          'longitude': r.longitude,
          'kind': r.kind.value,
          'smokeIntensity': r.smokeIntensity,
          'durationHours': r.durationHours,
          'reportedAt': r.reportedAt.toIso8601String(),
          'region': r.region,
          'photoBase64': photoBase64,
          'photoFilename': r.photoFilename,
          'notes': r.notes,
          'clientReportId': r.clientReportId,
        });
      }).toList();
      await _prefs!.setStringList(_keyReports, encoded);
    } catch (_) {}
  }
}

final citizenReportsStoreProvider = Provider<CitizenReportsStore>((ref) {
  return CitizenReportsStore();
});

class CitizenReportsNotifier extends StateNotifier<AsyncValue<List<FireReport>>> {
  CitizenReportsNotifier(this._store) : super(const AsyncValue.loading()) {
    load();
  }

  final CitizenReportsStore _store;

  Future<void> load() async {
    try {
      final list = await _store.getAllReports();
      state = AsyncValue.data(list);
    } catch (e, st) {
      state = AsyncValue.error(e, st);
    }
  }

  Future<void> addReport(FireReport report) async {
    await _store.saveReport(report);
    await load();
  }
}

final citizenReportsProvider =
    StateNotifierProvider<CitizenReportsNotifier, AsyncValue<List<FireReport>>>((ref) {
  return CitizenReportsNotifier(ref.watch(citizenReportsStoreProvider));
});
