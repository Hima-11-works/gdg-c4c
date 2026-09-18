import 'sensitivity.dart';
import 'user_alert_preferences.dart';
import 'user_sensitivity_profile.dart';

export 'sensitivity.dart';
export 'user_alert_preferences.dart';
export 'user_sensitivity_profile.dart' show UserSensitivityProfile, CustomSensitivityRules;

/// The user's on-device health profile.
///
/// Stored ONLY in [flutter_secure_storage] — never transmitted,
/// never logged, never included in analytics.
class UserProfile {
  const UserProfile({
    this.healthContext = UserHealthContext.none,
    this.sensitivity = AlertSensitivity.standard,
    this.customRules,
    this.preferences = const UserAlertPreferences(),
  });

  final UserHealthContext healthContext;
  final AlertSensitivity sensitivity;

  /// Only populated when [sensitivity] is [AlertSensitivity.custom].
  final CustomSensitivityRules? customRules;

  /// How alerts are delivered (master switch, quiet hours, minimum severity,
  /// recovery alerts). Stored on-device alongside the rest of the profile and
  /// fed to the alert engine.
  final UserAlertPreferences preferences;

  UserProfile copyWith({
    UserHealthContext? healthContext,
    AlertSensitivity? sensitivity,
    CustomSensitivityRules? customRules,
    UserAlertPreferences? preferences,
  }) {
    return UserProfile(
      healthContext: healthContext ?? this.healthContext,
      sensitivity: sensitivity ?? this.sensitivity,
      customRules: customRules ?? this.customRules,
      preferences: preferences ?? this.preferences,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is UserProfile &&
          healthContext == other.healthContext &&
          sensitivity == other.sensitivity &&
          customRules == other.customRules &&
          preferences == other.preferences;

  @override
  int get hashCode =>
      Object.hash(healthContext, sensitivity, customRules, preferences);

  @override
  String toString() =>
      'UserProfile(context=${healthContext.name}, sensitivity=${sensitivity.name})';
}
