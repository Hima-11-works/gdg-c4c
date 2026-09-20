import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'prefs_providers.dart';
import 'profile_providers.dart';

/// Whether the app can skip onboarding.
///
/// Requires **both**:
/// 1. the persisted `onboarding_done` flag, and
/// 2. a readable, non-null user profile.
///
/// The flag lives in `SharedPreferences`, which Android backs up and
/// restores to a new/restored device. The profile lives in secure storage,
/// which is **not** recoverable after a restore or a reinstall with a
/// different signing key. Requiring both stops a restored device from
/// silently skipping onboarding (and showing a broken/blank Settings screen)
/// when the flag was restored but no usable profile exists.
final onboardingCompleteProvider = FutureProvider<bool>((ref) async {
  final flag = await ref.watch(onboardingDoneProvider.future);
  if (!flag) return false;
  try {
    final profile = await ref.watch(userProfileProvider.future);
    return profile != null;
  } catch (_) {
    // Unreadable profile — treat as "not onboarded" so the user can
    // re-enter their details instead of landing on a broken Settings screen.
    return false;
  }
});
