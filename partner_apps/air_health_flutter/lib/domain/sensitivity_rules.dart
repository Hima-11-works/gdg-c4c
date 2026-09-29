import '../domain/models/models.dart';

/// Configurable thresholds derived from the user's sensitivity profile.
///
/// Each [AlertSensitivity] tier maps to a set of environmental
/// thresholds. The engine never invents disease-specific values —
/// it only shifts the CPCB category that triggers attention.
class SensitivityRules {
  const SensitivityRules({
    required this.warningCategory,
    required this.forecastCategory,
    required this.rapidRiseAqiPerHour,
    required this.leadTimePreference,
    required this.minForecastConfidence,
    required this.approachingEventLeadTime,
    this.cooldownMinutes = 90,
    this.hysteresisAqi = 10,
  });

  /// Earliest CPCB category that triggers a "current" alert.
  final CpcbCategory warningCategory;

  /// Earliest CPCB category in a forecast that triggers a forecast alert.
  final CpcbCategory forecastCategory;

  /// AQI-per-hour increase counted as "rapid rise".
  final int rapidRiseAqiPerHour;

  /// Maximum lead time the engine will report for a forecast alert.
  final Duration leadTimePreference;

  /// Minimum forecast confidence to trigger an alert.
  final double minForecastConfidence;

  /// Maximum lead time for an approaching-pollution event alert.
  final Duration approachingEventLeadTime;

  /// Minutes to wait before re-alerting for the same dedupKey.
  final int cooldownMinutes;

  /// AQI must drop this far below a threshold before the situation is
  /// considered "recovered" — prevents flapping at exact boundaries.
  final int hysteresisAqi;

  /// Build rules for the given [profile].
  ///
  /// [UserAlertPreferences.leadTime], when set, overrides the tier default
  /// for [leadTimePreference] (the "Forecast warning lead time" setting).
  factory SensitivityRules.forProfile(UserSensitivityProfile profile) {
    final leadOverride = profile.preferences.leadTime;
    if (profile.isCustom && profile.customRules != null) {
      final custom = profile.customRules!;
      return SensitivityRules(
        warningCategory: CpcbCategory.fromAqi(custom.warningAqi),
        forecastCategory: CpcbCategory.fromAqi(custom.forecastWarningAqi),
        rapidRiseAqiPerHour: custom.rapidRiseAqiPerHour,
        leadTimePreference: leadOverride ?? const Duration(hours: 3),
        minForecastConfidence: 0.6,
        approachingEventLeadTime: const Duration(hours: 3),
      );
    }

    // For non-normal patients, threshold and rapid rise alerts adapt directly
    // to their respiratory condition severity.
    if (profile.isPatient) {
      return switch (profile.effectiveDiseaseSeverity) {
        DiseaseSeverity.mild => SensitivityRules(
            warningCategory: CpcbCategory.moderate,
            forecastCategory: CpcbCategory.moderate,
            rapidRiseAqiPerHour: 20,
            leadTimePreference: leadOverride ?? const Duration(hours: 3),
            minForecastConfidence: 0.7,
            approachingEventLeadTime: const Duration(hours: 2),
          ),
        DiseaseSeverity.moderate => SensitivityRules(
            warningCategory: CpcbCategory.satisfactory,
            forecastCategory: CpcbCategory.moderate,
            rapidRiseAqiPerHour: 15,
            leadTimePreference: leadOverride ?? const Duration(hours: 4),
            minForecastConfidence: 0.6,
            approachingEventLeadTime: const Duration(hours: 3),
          ),
        DiseaseSeverity.severe => SensitivityRules(
            warningCategory: CpcbCategory.satisfactory,
            forecastCategory: CpcbCategory.satisfactory,
            rapidRiseAqiPerHour: 10,
            leadTimePreference: leadOverride ?? const Duration(hours: 6),
            minForecastConfidence: 0.5,
            approachingEventLeadTime: const Duration(hours: 4),
          ),
      };
    }

    return switch (profile.sensitivity) {
      AlertSensitivity.standard => SensitivityRules(
          warningCategory: CpcbCategory.poor,
          forecastCategory: CpcbCategory.poor,
          rapidRiseAqiPerHour: 40,
          leadTimePreference: leadOverride ?? const Duration(hours: 2),
          minForecastConfidence: 0.8,
          approachingEventLeadTime: const Duration(hours: 1),
        ),
      AlertSensitivity.sensitive => SensitivityRules(
          warningCategory: CpcbCategory.moderate,
          forecastCategory: CpcbCategory.moderate,
          rapidRiseAqiPerHour: 25,
          leadTimePreference: leadOverride ?? const Duration(hours: 3),
          minForecastConfidence: 0.7,
          approachingEventLeadTime: const Duration(hours: 2),
        ),
      AlertSensitivity.high => SensitivityRules(
          warningCategory: CpcbCategory.satisfactory,
          forecastCategory: CpcbCategory.moderate,
          rapidRiseAqiPerHour: 15,
          leadTimePreference: leadOverride ?? const Duration(hours: 6),
          minForecastConfidence: 0.5,
          approachingEventLeadTime: const Duration(hours: 4),
        ),
      AlertSensitivity.custom => SensitivityRules(
          warningCategory: CpcbCategory.moderate,
          forecastCategory: CpcbCategory.moderate,
          rapidRiseAqiPerHour: 25,
          leadTimePreference: leadOverride ?? const Duration(hours: 3),
          minForecastConfidence: 0.6,
          approachingEventLeadTime: const Duration(hours: 3),
        ),
    };
  }

  @override
  String toString() =>
      'SensitivityRules(warn=${warningCategory.name}, '
      'forecast=${forecastCategory.name}, rise=$rapidRiseAqiPerHour/hr, '
      'cooldown=${cooldownMinutes}m, hysteresis=$hysteresisAqi)';
}
