import '../core/formatters.dart';
import 'models/models.dart';

/// User-facing alert message — the complete text for a notification
/// or in-app alert card.
class AlertMessage {
  const AlertMessage({
    required this.title,
    required this.body,
    required this.lockScreenBody,
    required this.guidance,
  });

  /// Short headline (e.g. "Air quality worsening").
  final String title;

  /// Full message body shown in-app.
  final String body;

  /// Body safe for lock-screen display — never mentions health context
  /// or sensitivity settings.
  final String lockScreenBody;

  /// Exposure-aware follow-up advice.
  final String guidance;
}

/// Produces calm, non-diagnostic user-facing messages from
/// [AlertDecision]s.
///
/// Independent of [AlertEngine] — the engine decides WHAT happened,
/// this service decides HOW it is explained. Messages are templated,
/// not generated; no medical claims, no diagnosis, no prescriptions.
class AlertMessageService {
  const AlertMessageService();

  /// Build a user-facing message for the given [decision].
  ///
  /// [sensitivity] tailors the tone (e.g. sensitive profiles get an
  /// explanation of why they're being alerted early). The lock-screen
  /// body is always stripped of health/sensitivity references.
  /// Build a user-facing message for the given [decision].
  ///
  /// [sensitivity] tailors the tone (e.g. sensitive profiles get an
  /// explanation of why they're being alerted early). The lock-screen
  /// body is always stripped of health/sensitivity references.
  AlertMessage buildMessage({
    required AlertDecision decision,
    required AlertSensitivity sensitivity,
    UserHealthContext healthContext = UserHealthContext.none,
    DiseaseSeverity? diseaseSeverity,
  }) {
    final isPatient = healthContext != UserHealthContext.none &&
        healthContext != UserHealthContext.preferNotToSay;

    return switch (decision.trigger) {
      AlertTrigger.currentThreshold =>
        _currentThresholdMessage(decision, sensitivity, isPatient: isPatient, healthContext: healthContext, severity: diseaseSeverity),
      AlertTrigger.forecastThreshold =>
        _forecastThresholdMessage(decision, sensitivity, isPatient: isPatient, healthContext: healthContext, severity: diseaseSeverity),
      AlertTrigger.rapidRise =>
        _rapidRiseMessage(decision, sensitivity, isPatient: isPatient, healthContext: healthContext, severity: diseaseSeverity),
      AlertTrigger.approachingPollution =>
        _approachingPollutionMessage(decision, sensitivity),
      AlertTrigger.recovery => _recoveryMessage(decision, sensitivity),
    };
  }

  // ── Current threshold ───────────────────────────────────────────────

  AlertMessage _currentThresholdMessage(
    AlertDecision d,
    AlertSensitivity sensitivity, {
    bool isPatient = false,
    UserHealthContext healthContext = UserHealthContext.none,
    DiseaseSeverity? severity,
  }) {
    final catLabel = Formatters.categoryLabel(d.currentAqi);
    final title = isPatient
        ? 'AQI is rising: $catLabel'
        : 'Air quality: $catLabel';

    final body = StringBuffer()
      ..write('Air quality near you is currently $catLabel '
          '(AQI ${d.currentAqi}).')
      ..write(_sensitivitySuffix(sensitivity));

    if (isPatient) {
      body.write(' As a ${healthContext.label} patient, please take necessary precaution and action.');
    }

    final lockBody = isPatient
        ? 'AQI is rising (AQI ${d.currentAqi}, $catLabel). Please take necessary precaution and action.'
        : 'Air quality near you is currently $catLabel (AQI ${d.currentAqi}).';

    return AlertMessage(
      title: title,
      body: body.toString(),
      lockScreenBody: lockBody,
      guidance: isPatient
          ? 'Air quality is rising. Limit outdoor exposure, stay indoors, follow your care plan, and take necessary precaution and action.'
          : _guidanceForSeverity(d.severity),
    );
  }

  // ── Forecast threshold ──────────────────────────────────────────────

  AlertMessage _forecastThresholdMessage(
    AlertDecision d,
    AlertSensitivity sensitivity, {
    bool isPatient = false,
    UserHealthContext healthContext = UserHealthContext.none,
    DiseaseSeverity? severity,
  }) {
    final catLabel = Formatters.categoryLabel(d.predictedAqi ?? d.currentAqi);
    final timeStr = Formatters.leadTime(d.leadTime);
    final title = isPatient
        ? 'AQI is rising expected: $catLabel'
        : 'Air quality expected to worsen';

    final body = StringBuffer()
      ..write('Air quality near you is expected to reach $catLabel')
      ..write(timeStr.isNotEmpty ? ' in approximately $timeStr.' : ' soon.')
      ..write(_sensitivitySuffix(sensitivity));

    if (isPatient) {
      body.write(' Please prepare and take necessary precautions.');
    }

    final lockBody = isPatient
        ? 'AQI is rising and expected to reach $catLabel. Please take necessary precaution and action.'
        : (StringBuffer()
            ..write('Air quality near you is expected to reach $catLabel')
            ..write(timeStr.isNotEmpty ? ' in approximately $timeStr.' : ' soon.')).toString();

    return AlertMessage(
      title: title,
      body: body.toString(),
      lockScreenBody: lockBody,
      guidance: isPatient
          ? 'Air quality is rising. Reduce outdoor activity, prepare indoor air filtration, and follow your care plan.'
          : _guidanceForSeverity(d.severity),
    );
  }

  // ── Rapid rise ──────────────────────────────────────────────────────

  AlertMessage _rapidRiseMessage(
    AlertDecision d,
    AlertSensitivity sensitivity, {
    bool isPatient = false,
    UserHealthContext healthContext = UserHealthContext.none,
    DiseaseSeverity? severity,
  }) {
    final catLabel = Formatters.categoryLabel(d.predictedAqi ?? d.currentAqi);
    final timeStr = Formatters.leadTime(d.leadTime);
    final title = 'AQI is rising rapidly';

    final body = StringBuffer()
      ..write('Air quality near you is rising quickly')
      ..write(timeStr.isNotEmpty
          ? ' and may reach $catLabel within approximately $timeStr.'
          : ' and may reach $catLabel soon.')
      ..write(_sensitivitySuffix(sensitivity));

    if (isPatient) {
      body.write(' For your ${healthContext.label} (${severity?.label ?? 'Moderate'} severity), please take necessary precaution and action immediately.');
    }

    final lockBody =
        'AQI is rising rapidly nearby. Please take necessary precaution and action.';

    return AlertMessage(
      title: title,
      body: body.toString(),
      lockScreenBody: lockBody,
      guidance: isPatient
          ? 'Surrounding AQI is rising rapidly. Avoid outdoor activity, stay indoors, follow your care plan, and take necessary precaution and action.'
          : _guidanceForSeverity(d.severity),
    );
  }

  // ── Approaching pollution ───────────────────────────────────────────

  AlertMessage _approachingPollutionMessage(
    AlertDecision d,
    AlertSensitivity sensitivity,
  ) {
    final timeStr = Formatters.leadTime(d.leadTime);
    final title = 'Pollution approaching your area';

    final body = StringBuffer()
      ..write('Pollution from a nearby area is expected to reach you')
      ..write(timeStr.isNotEmpty
          ? ' in approximately $timeStr.'
          : ' soon.')
      ..write(_sensitivitySuffix(sensitivity));

    final lockBody = StringBuffer()
      ..write('Pollution from a nearby area is expected to reach you')
      ..write(timeStr.isNotEmpty
          ? ' in approximately $timeStr.'
          : ' soon.');

    return AlertMessage(
      title: title,
      body: body.toString(),
      lockScreenBody: lockBody.toString(),
      guidance: _guidanceForSeverity(d.severity),
    );
  }

  // ── Recovery ────────────────────────────────────────────────────────

  AlertMessage _recoveryMessage(
    AlertDecision d,
    AlertSensitivity sensitivity,
  ) {
    final catLabel = Formatters.categoryLabel(d.currentAqi);
    final title = 'Air quality improving';

    final body =
        'Air quality near you has improved to $catLabel '
        '(AQI ${d.currentAqi}). Conditions are currently more favourable.';

    return AlertMessage(
      title: title,
      body: body,
      lockScreenBody: body,
      guidance: 'Conditions have improved. You may resume normal activities '
          'according to your existing care plan.',
    );
  }

  // ── Helpers ─────────────────────────────────────────────────────────

  /// Appended to in-app body for sensitive/high profiles — explains
  /// WHY the user is being alerted early. Never appears on lock screen.
  static String _sensitivitySuffix(AlertSensitivity sensitivity) {
    return switch (sensitivity) {
      AlertSensitivity.standard => '',
      AlertSensitivity.sensitive =>
        " You've enabled early air-quality alerts, so we're notifying you "
            'before conditions worsen. Consider reducing prolonged outdoor '
            'exposure.',
      AlertSensitivity.high =>
        " You've enabled high-sensitivity alerts, so we're notifying you "
            'early. Consider reducing prolonged outdoor exposure and '
            'follow your existing care plan.',
      AlertSensitivity.custom =>
        ' You have custom alert thresholds configured.',
    };
  }

  static String _guidanceForSeverity(AlertSeverity severity) {
    return switch (severity) {
      AlertSeverity.info =>
        'Conditions are currently acceptable. No special precautions needed.',
      AlertSeverity.advisory =>
        'Consider reducing prolonged outdoor exposure and follow your '
        'existing care plan if needed.',
      AlertSeverity.warning =>
        'Reduce prolonged outdoor exposure. Limit time spent outside '
        'and follow your existing care plan.',
      AlertSeverity.urgent =>
        'Avoid outdoor activity. Stay indoors with windows closed. '
        'Follow your existing care plan.',
    };
  }

}
