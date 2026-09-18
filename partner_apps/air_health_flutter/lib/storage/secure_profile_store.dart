import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../domain/models/models.dart';

/// Persists the user's health profile in platform-secure storage.
///
/// On Android this uses EncryptedSharedPreferences; on iOS, Keychain.
/// The profile NEVER leaves the device.
class SecureProfileStore {
  SecureProfileStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  static const _keyProfile = 'user_profile';

  Future<void> save(UserProfile profile) async {
    final prefs = profile.preferences;
    await _storage.write(
      key: _keyProfile,
      value: jsonEncode({
        'healthContext': profile.healthContext.name,
        'sensitivity': profile.sensitivity.name,
        'preferences': {
          'alertsEnabled': prefs.alertsEnabled,
          'recoveryAlertsEnabled': prefs.recoveryAlertsEnabled,
          'minimumSeverity': prefs.minimumSeverity.name,
          'quietHoursStart': prefs.quietHoursStart?.toIso8601String(),
          'quietHoursEnd': prefs.quietHoursEnd?.toIso8601String(),
        },
      }),
    );
  }

  Future<UserProfile?> read() async {
    final raw = await _storage.read(key: _keyProfile);
    if (raw == null) return null;
    final map = jsonDecode(raw) as Map<String, dynamic>;
    return UserProfile(
      healthContext: UserHealthContext.values.firstWhere(
        (e) => e.name == map['healthContext'],
        orElse: () => UserHealthContext.none,
      ),
      sensitivity: AlertSensitivity.values.firstWhere(
        (e) => e.name == map['sensitivity'],
        orElse: () => AlertSensitivity.standard,
      ),
      preferences: _readPreferences(map['preferences']),
    );
  }

  /// Read the stored preferences defensively — profiles written before this
  /// field existed (or with a malformed value) fall back to defaults.
  static UserAlertPreferences _readPreferences(Object? raw) {
    if (raw is! Map) return const UserAlertPreferences();
    final map = raw.cast<String, dynamic>();
    return UserAlertPreferences(
      alertsEnabled: map['alertsEnabled'] as bool? ?? true,
      recoveryAlertsEnabled: map['recoveryAlertsEnabled'] as bool? ?? true,
      minimumSeverity: AlertSeverity.values.firstWhere(
        (e) => e.name == map['minimumSeverity'],
        orElse: () => AlertSeverity.info,
      ),
      quietHoursStart: _parseDate(map['quietHoursStart']),
      quietHoursEnd: _parseDate(map['quietHoursEnd']),
    );
  }

  static DateTime? _parseDate(Object? value) =>
      value is String ? DateTime.tryParse(value) : null;

  Future<void> delete() async {
    await _storage.delete(key: _keyProfile);
  }
}
