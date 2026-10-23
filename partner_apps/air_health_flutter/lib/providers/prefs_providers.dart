import 'package:flutter/foundation.dart';
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

/// The app language selected in Settings. English is the safe first-frame
/// fallback while the saved preference loads from SharedPreferences.
final appLanguageProvider = StateNotifierProvider<AppLanguageNotifier, String>(
  (ref) => AppLanguageNotifier(ref.read(prefsStoreProvider)),
);

class AppLanguageNotifier extends StateNotifier<String> {
  AppLanguageNotifier(this._store) : super('en') {
    _load();
  }

  final PrefsStore _store;

  Future<void> _load() async {
    try {
      final saved = await _store.getAppLanguage();
      if (mounted) state = saved;
    } catch (error, stackTrace) {
      debugPrint('Could not load app language: $error\n$stackTrace');
    }
  }

  Future<void> setLanguage(String code) async {
    await _store.setAppLanguage(code);
    state = code;
  }

  void resetLanguage() => state = 'en';
}
