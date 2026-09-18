import 'package:shared_preferences/shared_preferences.dart';

/// Lightweight non-sensitive preferences (currently: onboarding done).
///
/// NOT for health data — that goes in [UserProfileRepository].
class PrefsStore {
  PrefsStore({SharedPreferences? initialPrefs}) : _prefs = initialPrefs;

  SharedPreferences? _prefs;

  Future<SharedPreferences> get prefs async =>
      _prefs ??= await SharedPreferences.getInstance();

  // ── Onboarding ─────────────────────────────────────────────────────

  Future<bool> getOnboardingDone() async {
    return (await prefs).getBool('onboarding_done') ?? false;
  }

  Future<void> setOnboardingDone(bool value) async {
    await (await prefs).setBool('onboarding_done', value);
  }

  // ── Reset ──────────────────────────────────────────────────────────

  Future<void> resetAll() async {
    final p = await prefs;
    await p.remove('onboarding_done');
    // Defensive: clear any legacy UI-preference keys from older installs.
    await p.remove('units');
    await p.remove('dark_mode');
    await p.remove('reduced_motion');
  }
}
