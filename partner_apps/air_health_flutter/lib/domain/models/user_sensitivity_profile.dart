import 'sensitivity.dart';
import 'user_alert_preferences.dart';

/// Combined profile the alert engine receives as input.
///
/// Aggregates [UserHealthContext], [AlertSensitivity], and
/// [UserAlertPreferences] into one immutable value — the engine
/// never reads storage directly.
class UserSensitivityProfile {
  const UserSensitivityProfile({
    required this.healthContext,
    required this.sensitivity,
    required this.preferences,
    this.diseaseSeverity,
    this.customRules,
  });

  final UserHealthContext healthContext;
  final AlertSensitivity sensitivity;
  final UserAlertPreferences preferences;

  /// Disease severity for patients with respiratory/health conditions.
  final DiseaseSeverity? diseaseSeverity;

  /// Whether the user has any health context set.
  bool get hasHealthContext => healthContext != UserHealthContext.none;

  /// Whether this profile represents a non-normal patient with respiratory/health conditions.
  bool get isPatient =>
      healthContext != UserHealthContext.none &&
      healthContext != UserHealthContext.preferNotToSay;

  /// Effective disease severity for patients.
  DiseaseSeverity get effectiveDiseaseSeverity =>
      diseaseSeverity ?? DiseaseSeverity.moderate;

  /// Only populated when [sensitivity] is [AlertSensitivity.custom].
  final CustomSensitivityRules? customRules;

  /// Whether this profile uses the custom tier.
  bool get isCustom => sensitivity == AlertSensitivity.custom;

  UserSensitivityProfile copyWith({
    UserHealthContext? healthContext,
    AlertSensitivity? sensitivity,
    UserAlertPreferences? preferences,
    DiseaseSeverity? diseaseSeverity,
    CustomSensitivityRules? customRules,
  }) {
    return UserSensitivityProfile(
      healthContext: healthContext ?? this.healthContext,
      sensitivity: sensitivity ?? this.sensitivity,
      preferences: preferences ?? this.preferences,
      diseaseSeverity: diseaseSeverity ?? this.diseaseSeverity,
      customRules: customRules ?? this.customRules,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is UserSensitivityProfile &&
          healthContext == other.healthContext &&
          sensitivity == other.sensitivity &&
          preferences == other.preferences &&
          diseaseSeverity == other.diseaseSeverity &&
          customRules == other.customRules;

  @override
  int get hashCode => Object.hash(
        healthContext,
        sensitivity,
        preferences,
        diseaseSeverity,
        customRules,
      );

  @override
  String toString() =>
      'UserSensitivityProfile(context=${healthContext.name}, '
      'severity=${diseaseSeverity?.name}, '
      'sensitivity=${sensitivity.name}, prefs=$preferences)';
}

/// User-defined threshold overrides for the custom sensitivity tier.
class CustomSensitivityRules {
  const CustomSensitivityRules({
    this.warningAqi = 101,
    this.forecastWarningAqi = 151,
    this.rapidRiseAqiPerHour = 30,
  });

  final int warningAqi;
  final int forecastWarningAqi;
  final int rapidRiseAqiPerHour;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is CustomSensitivityRules &&
          warningAqi == other.warningAqi &&
          forecastWarningAqi == other.forecastWarningAqi &&
          rapidRiseAqiPerHour == other.rapidRiseAqiPerHour;

  @override
  int get hashCode =>
      Object.hash(warningAqi, forecastWarningAqi, rapidRiseAqiPerHour);

  @override
  String toString() =>
      'CustomSensitivityRules(warn=$warningAqi, forecast=$forecastWarningAqi, rise=$rapidRiseAqiPerHour/hr)';
}
