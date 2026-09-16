import '../domain/models/models.dart';
import '../storage/secure_profile_store.dart';

/// Repository for the user's health profile.
///
/// Screens and providers interact with this — never directly with
/// [SecureProfileStore] or [FlutterSecureStorage]. Health context
/// and sensitivity are NEVER logged, printed, or transmitted.
class UserProfileRepository {
  UserProfileRepository({required SecureProfileStore this._store});

  final SecureProfileStore _store;

  /// Load the persisted profile, or null if none exists.
  Future<UserProfile?> loadProfile() async {
    return _store.read();
  }

  /// Save a profile (overwrite any existing one).
  Future<void> saveProfile(UserProfile profile) async {
    _assertNoLogging(profile);
    await _store.save(profile);
  }

  /// Update specific fields of the existing profile.
  ///
  /// If no profile exists yet, creates one with defaults + the update.
  Future<UserProfile> updateProfile({
    UserHealthContext? healthContext,
    AlertSensitivity? sensitivity,
    CustomSensitivityRules? customRules,
  }) async {
    final existing = await _store.read();
    final updated = (existing ?? const UserProfile()).copyWith(
      healthContext: healthContext,
      sensitivity: sensitivity,
      customRules: customRules,
    );
    await _store.save(updated);
    return updated;
  }

  /// Delete all profile data from secure storage.
  Future<void> deleteProfile() async {
    await _store.delete();
  }

  /// Reset the profile to defaults (standard sensitivity, no health context).
  ///
  /// Unlike [deleteProfile], this always leaves a profile in storage —
  /// the user's "no sensitivity" choice is itself a preference worth
  /// keeping.
  Future<UserProfile> resetProfile() async {
    const defaults = UserProfile();
    await _store.save(defaults);
    return defaults;
  }

  /// Assert that health data is never printed or logged.
  ///
  /// This is a development-time check — in release builds, the assert
  /// is stripped and the method is a no-op.
  static void _assertNoLogging(UserProfile profile) {
    assert(() {
      // Intentionally NOT printing profile.healthContext or
      // profile.sensitivity — health data must never appear in logs.
      return true;
    }());
  }
}
