import '../domain/models/models.dart';
import 'sensitivity_rules.dart';

/// Pure-Dart alert engine — no Flutter, no I/O, no storage.
///
/// Takes the current environmental state and a user profile, returns
/// zero or more [AlertDecision]s. The caller (a Riverpod provider)
/// is responsible for deduplication, cooldown, and notification dispatch.
class AlertEngine {
  const AlertEngine();

  /// Evaluate all alert rules and return every decision that should fire.
  ///
  /// Rules run in priority order; the caller may further filter by
  /// [UserAlertPreferences.minimumSeverity].
  ///
  /// [now] defaults to [DateTime.now()] but can be overridden for tests.
  List<AlertDecision> evaluate({
    required UserSensitivityProfile profile,
    required AirQualityReading current,
    required List<ForecastPoint> forecast,
    required List<PollutionEvent> events,
    required DataFreshness freshness,
    DateTime? now,
  }) {
    if (!profile.preferences.alertsEnabled) return const [];
    final effectiveNow = now ?? DateTime.now();

    final rules = SensitivityRules.forProfile(profile);
    final decisions = <AlertDecision>[];

    // 1. Current threshold.
    final currentDecision = _checkCurrentThreshold(
      current: current,
      rules: rules,
      now: effectiveNow,
    );
    if (currentDecision != null) decisions.add(currentDecision);

    // 2. Forecast threshold.
    final forecastDecision = _checkForecastThreshold(
      current: current,
      forecast: forecast,
      rules: rules,
      now: effectiveNow,
    );
    if (forecastDecision != null) decisions.add(forecastDecision);

    // 3. Rapid rise.
    final rapidRiseDecision = _checkRapidRise(
      current: current,
      forecast: forecast,
      rules: rules,
      now: effectiveNow,
    );
    if (rapidRiseDecision != null) decisions.add(rapidRiseDecision);

    // 4. Approaching pollution.
    final approachingDecisions = _checkApproachingPollution(
      current: current,
      events: events,
      rules: rules,
      now: effectiveNow,
    );
    decisions.addAll(approachingDecisions);

    // 5. Recovery.
    if (profile.preferences.recoveryAlertsEnabled) {
      // Recovery needs prior alert state — skip if we have none.
      // The caller tracks dedup state and passes it in if needed.
    }

    return decisions;
  }

  // ── Rule 1: Current threshold ────────────────────────────────────────

  AlertDecision? _checkCurrentThreshold({
    required AirQualityReading current,
    required SensitivityRules rules,
    required DateTime now,
  }) {
    final category = current.category;
    if (category.index < rules.warningCategory.index) return null;

    final severity = _severityForCategory(category);
    return AlertDecision(
      shouldAlert: true,
      severity: severity,
      trigger: AlertTrigger.currentThreshold,
      currentAqi: current.aqiCpcb,
      confidence: 1.0,
      messageContext: 'Current air quality is ${category.label} '
          '(AQI ${current.aqiCpcb}).',
      dedupKey: 'current_${category.name}',
      guidance: _guidanceForSeverity(severity),
    );
  }

  // ── Rule 2: Forecast threshold ───────────────────────────────────────

  AlertDecision? _checkForecastThreshold({
    required AirQualityReading current,
    required List<ForecastPoint> forecast,
    required SensitivityRules rules,
    required DateTime now,
  }) {
    for (final point in forecast) {
      if (point.category.index >= rules.forecastCategory.index &&
          point.confidence >= rules.minForecastConfidence) {
        final lead = point.at.difference(now);
        if (lead > rules.leadTimePreference) continue;

        return AlertDecision(
          shouldAlert: true,
          severity: AlertSeverity.advisory,
          trigger: AlertTrigger.forecastThreshold,
          currentAqi: current.aqiCpcb,
          predictedAqi: point.aqiCpcb,
          predictedTime: point.at,
          leadTime: lead,
          confidence: point.confidence,
          messageContext:
              'Air quality is expected to reach ${point.category.label} '
              'around ${_formatTime(point.at)}.',
          dedupKey: 'forecast_${point.category.name}_${_dateKey(point.at)}',
          guidance: _guidanceForSeverity(AlertSeverity.advisory),
        );
      }
    }
    return null;
  }

  // ── Rule 3: Rapid rise ───────────────────────────────────────────────

  AlertDecision? _checkRapidRise({
    required AirQualityReading current,
    required List<ForecastPoint> forecast,
    required SensitivityRules rules,
    required DateTime now,
  }) {
    if (forecast.isEmpty) return null;
    // Check if AQI rises faster than the threshold in the first 3 hours.
    final earlyPoints = forecast.where(
      (f) => f.at.difference(now).inHours <= 3,
    );
    if (earlyPoints.isEmpty) return null;

    final maxRise = earlyPoints
        .map((f) => f.aqiCpcb - current.aqiCpcb)
        .reduce((a, b) => a > b ? a : b);

    if (maxRise < rules.rapidRiseAqiPerHour * 2) return null;

    final peak = earlyPoints.reduce(
      (a, b) => a.aqiCpcb > b.aqiCpcb ? a : b,
    );

    return AlertDecision(
      shouldAlert: true,
      severity: AlertSeverity.warning,
      trigger: AlertTrigger.rapidRise,
      currentAqi: current.aqiCpcb,
      predictedAqi: peak.aqiCpcb,
      predictedTime: peak.at,
      leadTime: peak.at.difference(now),
      confidence: peak.confidence,
      messageContext:
          'Air quality is rising rapidly — expected to reach '
          '${peak.category.label} within a few hours.',
      dedupKey: 'rapid_rise_${_dateKey(peak.at)}',
      guidance: _guidanceForSeverity(AlertSeverity.warning),
    );
  }

  // ── Rule 4: Approaching pollution ────────────────────────────────────

  List<AlertDecision> _checkApproachingPollution({
    required AirQualityReading current,
    required List<PollutionEvent> events,
    required SensitivityRules rules,
    required DateTime now,
  }) {
    final decisions = <AlertDecision>[];
    for (final event in events) {
      final lead = event.expectedArrivalAt.difference(now);
      if (lead > rules.approachingEventLeadTime) continue;

      decisions.add(AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.approachingPollution,
        currentAqi: current.aqiCpcb,
        predictedAqi: event.peakAqiEstimate,
        predictedTime: event.expectedArrivalAt,
        leadTime: lead,
        confidence: event.confidence,
        messageContext:
            'Pollution from ${event.sourceArea} is expected to reach '
            'your area around ${_formatTime(event.expectedArrivalAt)}.',
        dedupKey: 'approaching_${event.id}',
        guidance: _guidanceForSeverity(AlertSeverity.warning),
      ));
    }
    return decisions;
  }

  // ── Helpers ──────────────────────────────────────────────────────────

  static AlertSeverity _severityForCategory(CpcbCategory cat) {
    return switch (cat) {
      CpcbCategory.good => AlertSeverity.info,
      CpcbCategory.satisfactory => AlertSeverity.info,
      CpcbCategory.moderate => AlertSeverity.advisory,
      CpcbCategory.poor => AlertSeverity.warning,
      CpcbCategory.veryPoor => AlertSeverity.urgent,
      CpcbCategory.severe => AlertSeverity.urgent,
    };
  }

  static String _guidanceForSeverity(AlertSeverity severity) {
    return switch (severity) {
      AlertSeverity.info =>
        'Air quality is acceptable. No special precautions needed.',
      AlertSeverity.advisory =>
        'Consider reducing prolonged outdoor exposure and follow your '
        'existing care plan if needed.',
      AlertSeverity.warning =>
        'Reduce prolonged outdoor exposure. Follow your existing care plan '
        'and keep any prescribed treatments accessible.',
      AlertSeverity.urgent =>
        'Avoid outdoor activity. Stay indoors with windows closed. '
        'Follow your existing care plan and seek medical attention if needed.',
    };
  }

  static String _formatTime(DateTime dt) {
    final hour = dt.hour;
    final minute = dt.minute.toString().padLeft(2, '0');
    final period = hour >= 12 ? 'PM' : 'AM';
    final h = hour > 12 ? hour - 12 : (hour == 0 ? 12 : hour);
    return '$h:$minute $period';
  }

  static String _dateKey(DateTime dt) =>
      '${dt.year}${dt.month.toString().padLeft(2, '0')}${dt.day.toString().padLeft(2, '0')}';
}
