import 'package:shared_preferences/shared_preferences.dart';

/// Lightweight non-sensitive preferences (onboarding done, sort mode, etc.).
///
/// NOT for health data — that goes in [SecureProfileStore].
class PrefsStore {
  PrefsStore({SharedPreferences? initialPrefs}) : _prefs = initialPrefs;

  SharedPreferences? _prefs;

  Future<SharedPreferences> get prefs async =>
      _prefs ??= await SharedPreferences.getInstance();

  Future<bool> getOnboardingDone() async {
    return (await prefs).getBool('onboarding_done') ?? false;
  }

  Future<void> setOnboardingDone(bool value) async {
    await (await prefs).setBool('onboarding_done', value);
  }
}
