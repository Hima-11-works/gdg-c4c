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
    final custom = profile.customRules;
    await _storage.write(
      key: _keyProfile,
      value: jsonEncode({
        'healthContext': profile.healthContext.name,
        'sensitivity': profile.sensitivity.name,
        'customRules': custom == null
            ? null
            : {
                'warningAqi': custom.warningAqi,
                'forecastWarningAqi': custom.forecastWarningAqi,
                'rapidRiseAqiPerHour': custom.rapidRiseAqiPerHour,
              },
        'preferences': {
          'alertsEnabled': prefs.alertsEnabled,
          'recoveryAlertsEnabled': prefs.recoveryAlertsEnabled,
          'minimumSeverity': prefs.minimumSeverity.name,
          'quietHoursStart': prefs.quietHoursStart?.toIso8601String(),
          'quietHoursEnd': prefs.quietHoursEnd?.toIso8601String(),
          'leadTimeMinutes': prefs.leadTime?.inMinutes,
        },
      }),
    );
  }

  Future<UserProfile?> read() async {
    String? raw;
    try {
      raw = await _storage.read(key: _keyProfile);
    } catch (_) {
      // The keystore key that encrypted the stored value is no longer
      // valid — e.g. the app was reinstalled with a different signing
      // key, the device was restored from a backup, or the OS was
      // upgraded. The value cannot be decrypted, so drop it and start
      // fresh rather than crashing the whole settings screen.
      await _safeDelete();
      return null;
    }
    if (raw == null) return null;
    try {
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
        customRules: _readCustomRules(map['customRules']),
        preferences: _readPreferences(map['preferences']),
      );
    } catch (_) {
      // Corrupt or unparseable value — treat as "no profile".
      await _safeDelete();
      return null;
    }
  }

  /// Best-effort delete that never throws — used when the stored value is
  /// already unreadable, so a failure to clean it up must not mask the
  /// original problem.
  Future<void> _safeDelete() async {
    try {
      await _storage.delete(key: _keyProfile);
    } catch (_) {
      // Ignore — the next save() overwrites the key.
    }
  }

  /// Read stored custom sensitivity rules, or null if the profile never had
  /// any (or the value is malformed).
  static CustomSensitivityRules? _readCustomRules(Object? raw) {
    if (raw is! Map) return null;
    final map = raw.cast<String, dynamic>();
    return CustomSensitivityRules(
      warningAqi: (map['warningAqi'] as num?)?.toInt() ?? 101,
      forecastWarningAqi: (map['forecastWarningAqi'] as num?)?.toInt() ?? 151,
      rapidRiseAqiPerHour:
          (map['rapidRiseAqiPerHour'] as num?)?.toInt() ?? 30,
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
      leadTime: _parseDuration(map['leadTimeMinutes']),
    );
  }

  static DateTime? _parseDate(Object? value) =>
      value is String ? DateTime.tryParse(value) : null;

  static Duration? _parseDuration(Object? value) =>
      value is num ? Duration(minutes: value.toInt()) : null;

  Future<void> delete() async {
    await _safeDelete();
  }
}
