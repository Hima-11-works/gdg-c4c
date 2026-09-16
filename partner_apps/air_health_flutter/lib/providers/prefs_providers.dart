import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../storage/prefs_store.dart';

/// PrefsStore instance.
final prefsStoreProvider = Provider<PrefsStore>((ref) {
  return PrefsStore();
});

/// Whether onboarding has been completed.
final onboardingDoneProvider = FutureProvider<bool>((ref) async {
  final store = ref.read(prefsStoreProvider);
  return store.getOnboardingDone();
});
