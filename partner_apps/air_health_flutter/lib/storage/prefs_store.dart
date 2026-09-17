import 'package:shared_preferences/shared_preferences.dart';

/// Lightweight non-sensitive preferences (onboarding done, units, UI prefs).
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

  // ── Units ──────────────────────────────────────────────────────────

  Future<String> getUnits() async {
    return (await prefs).getString('units') ?? 'metric';
  }

  Future<void> setUnits(String value) async {
    await (await prefs).setString('units', value);
  }

  // ── UI preferences ─────────────────────────────────────────────────

  Future<bool> getDarkMode() async {
    return (await prefs).getBool('dark_mode') ?? false;
  }

  Future<void> setDarkMode(bool value) async {
    await (await prefs).setBool('dark_mode', value);
  }

  Future<bool> getReducedMotion() async {
    return (await prefs).getBool('reduced_motion') ?? false;
  }

  Future<void> setReducedMotion(bool value) async {
    await (await prefs).setBool('reduced_motion', value);
  }

  // ── Reset ──────────────────────────────────────────────────────────

  Future<void> resetAll() async {
    final p = await prefs;
    await p.remove('onboarding_done');
    await p.remove('units');
    await p.remove('dark_mode');
    await p.remove('reduced_motion');
  }
}
