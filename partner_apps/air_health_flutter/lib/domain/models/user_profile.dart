import 'sensitivity.dart';

/// The user's on-device health profile.
///
/// Stored ONLY in [flutter_secure_storage] — never transmitted,
/// never logged, never included in analytics.
class UserProfile {
  const UserProfile({
    this.healthContext = HealthContext.none,
    this.sensitivityTier = SensitivityTier.standard,
    this.customRules,
  });

  final HealthContext healthContext;
  final SensitivityTier sensitivityTier;

  /// Only populated when [sensitivityTier] is [SensitivityTier.custom].
  final CustomSensitivityRules? customRules;

  UserProfile copyWith({
    HealthContext? healthContext,
    SensitivityTier? sensitivityTier,
    CustomSensitivityRules? customRules,
  }) {
    return UserProfile(
      healthContext: healthContext ?? this.healthContext,
      sensitivityTier: sensitivityTier ?? this.sensitivityTier,
      customRules: customRules ?? this.customRules,
    );
  }
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
}
