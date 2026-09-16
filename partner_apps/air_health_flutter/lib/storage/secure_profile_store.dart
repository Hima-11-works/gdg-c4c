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
    await _storage.write(
      key: _keyProfile,
      value: jsonEncode({
        'healthContext': profile.healthContext.name,
        'sensitivityTier': profile.sensitivityTier.name,
      }),
    );
  }

  Future<UserProfile?> read() async {
    final raw = await _storage.read(key: _keyProfile);
    if (raw == null) return null;
    final map = jsonDecode(raw) as Map<String, dynamic>;
    return UserProfile(
      healthContext: HealthContext.values.firstWhere(
        (e) => e.name == map['healthContext'],
        orElse: () => HealthContext.none,
      ),
      sensitivityTier: SensitivityTier.values.firstWhere(
        (e) => e.name == map['sensitivityTier'],
        orElse: () => SensitivityTier.standard,
      ),
    );
  }

  Future<void> delete() async {
    await _storage.delete(key: _keyProfile);
  }
}
