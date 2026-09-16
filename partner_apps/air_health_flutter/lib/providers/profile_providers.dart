import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/models/models.dart';
import '../storage/secure_profile_store.dart';

/// Secure profile store instance.
final secureProfileStoreProvider = Provider<SecureProfileStore>((ref) {
  return SecureProfileStore();
});

/// Async-loaded user profile — null means "not yet loaded" or "no profile".
final userProfileProvider =
    AsyncNotifierProvider<UserProfileNotifier, UserProfile?>(
  UserProfileNotifier.new,
);

class UserProfileNotifier extends AsyncNotifier<UserProfile?> {
  @override
  Future<UserProfile?> build() async {
    final store = ref.read(secureProfileStoreProvider);
    return store.read();
  }

  Future<void> updateProfile(UserProfile profile) async {
    final store = ref.read(secureProfileStoreProvider);
    await store.save(profile);
    state = AsyncData(profile);
  }

  Future<void> deleteProfile() async {
    final store = ref.read(secureProfileStoreProvider);
    await store.delete();
    state = const AsyncData(null);
  }
}
