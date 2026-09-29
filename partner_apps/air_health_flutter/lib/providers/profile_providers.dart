import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/models/models.dart';
import '../storage/secure_profile_store.dart';
import '../storage/user_profile_repository.dart';

/// Secure profile store instance (low-level storage).
final secureProfileStoreProvider = Provider<SecureProfileStore>((ref) {
  return SecureProfileStore();
});

/// UserProfileRepository — the interface screens and providers use.
final userProfileRepositoryProvider = Provider<UserProfileRepository>((ref) {
  return UserProfileRepository(store: ref.read(secureProfileStoreProvider));
});

/// Async-loaded user profile — null means "not yet loaded" or "no profile".
final userProfileProvider =
    AsyncNotifierProvider<UserProfileNotifier, UserProfile?>(
  UserProfileNotifier.new,
);

class UserProfileNotifier extends AsyncNotifier<UserProfile?> {
  @override
  Future<UserProfile?> build() async {
    final repo = ref.read(userProfileRepositoryProvider);
    return repo.loadProfile();
  }

  Future<void> updateProfile(UserProfile profile) async {
    final repo = ref.read(userProfileRepositoryProvider);
    await repo.saveProfile(profile);
    state = AsyncData(profile);
  }

  Future<void> updateFields({
    UserHealthContext? healthContext,
    AlertSensitivity? sensitivity,
    CustomSensitivityRules? customRules,
    UserAlertPreferences? preferences,
    DiseaseSeverity? diseaseSeverity,
  }) async {
    final repo = ref.read(userProfileRepositoryProvider);
    final updated = await repo.updateProfile(
      healthContext: healthContext,
      sensitivity: sensitivity,
      customRules: customRules,
      preferences: preferences,
      diseaseSeverity: diseaseSeverity,
    );
    state = AsyncData(updated);
  }

  Future<void> deleteProfile() async {
    final repo = ref.read(userProfileRepositoryProvider);
    await repo.deleteProfile();
    state = const AsyncData(null);
  }

  Future<void> resetProfile() async {
    final repo = ref.read(userProfileRepositoryProvider);
    final defaults = await repo.resetProfile();
    state = AsyncData(defaults);
  }
}
