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
  factory SensitivityRules.forProfile(UserSensitivityProfile profile) {
    if (profile.isCustom && profile.customRules != null) {
      final custom = profile.customRules!;
      return SensitivityRules(
        warningCategory: CpcbCategory.fromAqi(custom.warningAqi),
        forecastCategory: CpcbCategory.fromAqi(custom.forecastWarningAqi),
        rapidRiseAqiPerHour: custom.rapidRiseAqiPerHour,
        leadTimePreference: const Duration(hours: 3),
        minForecastConfidence: 0.6,
        approachingEventLeadTime: const Duration(hours: 3),
      );
    }

    return switch (profile.sensitivity) {
      AlertSensitivity.standard => const SensitivityRules(
          warningCategory: CpcbCategory.poor,
          forecastCategory: CpcbCategory.poor,
          rapidRiseAqiPerHour: 40,
          leadTimePreference: Duration(hours: 2),
          minForecastConfidence: 0.8,
          approachingEventLeadTime: Duration(hours: 1),
        ),
      AlertSensitivity.sensitive => const SensitivityRules(
          warningCategory: CpcbCategory.moderate,
          forecastCategory: CpcbCategory.moderate,
          rapidRiseAqiPerHour: 25,
          leadTimePreference: Duration(hours: 3),
          minForecastConfidence: 0.7,
          approachingEventLeadTime: Duration(hours: 2),
        ),
      AlertSensitivity.high => const SensitivityRules(
          warningCategory: CpcbCategory.satisfactory,
          forecastCategory: CpcbCategory.moderate,
          rapidRiseAqiPerHour: 15,
          leadTimePreference: Duration(hours: 6),
          minForecastConfidence: 0.5,
          approachingEventLeadTime: Duration(hours: 4),
        ),
      AlertSensitivity.custom => const SensitivityRules(
          warningCategory: CpcbCategory.moderate,
          forecastCategory: CpcbCategory.moderate,
          rapidRiseAqiPerHour: 25,
          leadTimePreference: Duration(hours: 3),
          minForecastConfidence: 0.6,
          approachingEventLeadTime: Duration(hours: 3),
        ),
    };
  }

  @override
  String toString() =>
      'SensitivityRules(warn=${warningCategory.name}, '
      'forecast=${forecastCategory.name}, rise=$rapidRiseAqiPerHour/hr, '
      'cooldown=${cooldownMinutes}m, hysteresis=$hysteresisAqi)';
}
