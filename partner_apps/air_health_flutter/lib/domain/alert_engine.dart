import '../domain/models/models.dart';
import 'sensitivity_rules.dart';

export 'sensitivity_rules.dart';

/// Result of an engine evaluation — decisions plus updated dedup state.
class AlertEngineResult {
  const AlertEngineResult({
    required this.decisions,
    required this.dedupState,
  });

  /// Decisions that should fire (after cooldown/dedup filtering).
  final List<AlertDecision> decisions;

  /// Updated dedup state — pass back into the next evaluation.
  final List<DedupEntry> dedupState;
}

/// Pure-Dart alert engine — no Flutter, no I/O, no storage.
///
/// Takes the current environmental state, user profile, and prior
/// alert state, returns filtered decisions plus updated dedup state.
///
/// Supports: cooldown, deduplication, hysteresis, severity escalation,
/// recovery detection, minimum-severity filtering and quiet hours.
class AlertEngine {
  const AlertEngine();

  /// Evaluate all alert rules and return decisions that should fire.
  ///
  /// [priorAlerts] is the dedup state from previous runs — the engine
  /// filters duplicates and cooldown violations, and returns updated
  /// state in [AlertEngineResult.dedupState].
  ///
  /// [now] defaults to [DateTime.now()] but can be overridden for tests.
  AlertEngineResult evaluate({
    required UserSensitivityProfile profile,
    required AirQualityReading current,
    required List<ForecastPoint> forecast,
    required List<PollutionEvent> events,
    required DataFreshness freshness,
    List<DedupEntry> priorAlerts = const [],
    DateTime? now,
  }) {
    if (!profile.preferences.alertsEnabled) {
      return AlertEngineResult(decisions: const [], dedupState: priorAlerts);
    }
    final effectiveNow = now ?? DateTime.now();
    final rules = SensitivityRules.forProfile(profile);

    // 1. Generate all candidate decisions.
    final candidates = <AlertDecision>[
      ..._checkCurrentThreshold(
          current: current, rules: rules, profile: profile, now: effectiveNow),
      ..._checkForecastThreshold(
          current: current,
          forecast: forecast,
          rules: rules,
          now: effectiveNow),
      ..._checkRapidRise(
          current: current,
          forecast: forecast,
          rules: rules,
          profile: profile,
          now: effectiveNow),
      ..._checkApproachingPollution(
          current: current,
          events: events,
          rules: rules,
          now: effectiveNow),
      ..._checkRecovery(
          current: current,
          priorAlerts: priorAlerts,
          rules: rules,
          profile: profile,
          now: effectiveNow),
    ];

    // 2. Apply delivery preferences BEFORE dedup: a suppressed alert must not
    // be recorded as "already sent", otherwise it could never fire once the
    // suppression window ends.
    final deliverable = candidates.where((candidate) {
      if (candidate.severity < profile.preferences.minimumSeverity) {
        return false;
      }
      // Quiet hours silence everything except urgent (Very Poor/Severe)
      // air quality, which still gets through.
      if (candidate.severity != AlertSeverity.urgent &&
          profile.preferences.isWithinQuietHours(effectiveNow)) {
        return false;
      }
      return true;
    }).toList();

    // 3. Filter by cooldown and dedup.
    final filtered = <AlertDecision>[];
    final updatedDedup = List<DedupEntry>.from(priorAlerts);

    for (final candidate in deliverable) {
      final existing = _findExisting(updatedDedup, candidate.dedupKey);

      if (existing != null) {
        // Cooldown check.
        final elapsed =
            effectiveNow.difference(existing.lastAlertedAt).inMinutes;
        if (elapsed < rules.cooldownMinutes) {
          // Escalation check — if severity increased, allow through.
          if (!_isEscalation(existing, candidate)) continue;
        }
      }

      // Hysteresis — don't re-alert for the same category if we just
      // recovered from it (unless severity escalated).
      if (_isHysteresisViolation(candidate, priorAlerts, rules)) continue;

      filtered.add(candidate);

      // Update dedup state.
      if (existing != null) {
        updatedDedup.remove(existing);
        updatedDedup.add(DedupEntry(
          key: candidate.dedupKey,
          lastAlertedAt: effectiveNow,
          escalated: candidate.severity.index > AlertSeverity.advisory.index,
        ));
      } else {
        updatedDedup.add(DedupEntry(
          key: candidate.dedupKey,
          lastAlertedAt: effectiveNow,
          escalated: candidate.severity.index > AlertSeverity.advisory.index,
        ));
      }
    }

    // 3. Clean stale entries (older than 24h).
    updatedDedup.removeWhere(
      (e) => effectiveNow.difference(e.lastAlertedAt).inHours > 24,
    );

    return AlertEngineResult(
      decisions: filtered,
      dedupState: updatedDedup,
    );
  }

  // ── Rule 1: Current threshold ────────────────────────────────────────

  List<AlertDecision> _checkCurrentThreshold({
    required AirQualityReading current,
    required SensitivityRules rules,
    required DateTime now,
    UserSensitivityProfile? profile,
  }) {
    final category = current.category;
    if (category.index < rules.warningCategory.index) return const [];

    final severity = severityForCategory(category);
    final isPatient = profile?.isPatient ?? false;
    final contextMessage = isPatient
        ? 'AQI is rising in your surrounding (${category.label}, AQI ${current.aqiCpcb}). Please take necessary precautions and action.'
        : 'Current air quality is ${category.label} (AQI ${current.aqiCpcb}).';
    final guidanceMessage = isPatient
        ? 'AQI in your surrounding is rising. Limit outdoor exposure, stay indoors, follow your care plan, and take necessary precaution and action.'
        : _guidanceForSeverity(severity);

    return [
      AlertDecision(
        shouldAlert: true,
        severity: severity,
        trigger: AlertTrigger.currentThreshold,
        currentAqi: current.aqiCpcb,
        confidence: 1.0,
        messageContext: contextMessage,
        dedupKey: 'current_${category.name}',
        guidance: guidanceMessage,
      ),
    ];
  }

  // ── Rule 2: Forecast threshold ───────────────────────────────────────

  List<AlertDecision> _checkForecastThreshold({
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

        return [
          AlertDecision(
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
            dedupKey:
                'forecast_${point.category.name}_${_dateKey(point.at)}',
            guidance: _guidanceForSeverity(AlertSeverity.advisory),
          ),
        ];
      }
    }
    return const [];
  }

  // ── Rule 3: Rapid rise ───────────────────────────────────────────────

  List<AlertDecision> _checkRapidRise({
    required AirQualityReading current,
    required List<ForecastPoint> forecast,
    required SensitivityRules rules,
    required DateTime now,
    UserSensitivityProfile? profile,
  }) {
    if (forecast.isEmpty) return const [];
    final earlyPoints =
        forecast.where((f) => f.at.difference(now).inHours <= 3).toList();
    if (earlyPoints.isEmpty) return const [];

    final maxRise = earlyPoints
        .map((f) => f.aqiCpcb - current.aqiCpcb)
        .reduce((a, b) => a > b ? a : b);

    if (maxRise < rules.rapidRiseAqiPerHour * 2) return const [];

    final peak =
        earlyPoints.reduce((a, b) => a.aqiCpcb > b.aqiCpcb ? a : b);

    final isPatient = profile?.isPatient ?? false;
    final contextMessage = isPatient
        ? 'AQI is rising rapidly in your surrounding — please take necessary precautions and action.'
        : 'Air quality is rising rapidly — expected to reach ${peak.category.label} within a few hours.';
    final guidanceMessage = isPatient
        ? 'AQI is rising rapidly nearby. Avoid outdoor exposure, stay indoors, follow your care plan, and take necessary precaution and action.'
        : _guidanceForSeverity(AlertSeverity.warning);

    return [
      AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.rapidRise,
        currentAqi: current.aqiCpcb,
        predictedAqi: peak.aqiCpcb,
        predictedTime: peak.at,
        leadTime: peak.at.difference(now),
        confidence: peak.confidence,
        messageContext: contextMessage,
        dedupKey: 'rapid_rise_${_dateKey(peak.at)}',
        guidance: guidanceMessage,
      ),
    ];
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

  // ── Rule 5: Recovery ─────────────────────────────────────────────────

  List<AlertDecision> _checkRecovery({
    required AirQualityReading current,
    required List<DedupEntry> priorAlerts,
    required SensitivityRules rules,
    required UserSensitivityProfile profile,
    required DateTime now,
  }) {
    if (!profile.preferences.recoveryAlertsEnabled) return const [];
    if (priorAlerts.isEmpty) return const [];

    // Check if we previously alerted for a "current threshold" situation
    // and the AQI has now dropped below the hysteresis band.
    final currentPrior =
        priorAlerts.where((a) => a.key.startsWith('current_')).toList();
    if (currentPrior.isEmpty) return const [];

    final hysteresisFloor =
        _categoryLowerBound(rules.warningCategory) - rules.hysteresisAqi;
    if (current.aqiCpcb > hysteresisFloor) return const [];

    // Recovery confirmed.
    return [
      AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.info,
        trigger: AlertTrigger.recovery,
        currentAqi: current.aqiCpcb,
        confidence: 1.0,
        messageContext:
            'Air quality has improved to ${current.category.label} '
            '(AQI ${current.aqiCpcb}).',
        dedupKey: 'recovery_${current.category.name}',
        guidance: _guidanceForSeverity(AlertSeverity.info),
      ),
    ];
  }

  // ── Dedup / cooldown / escalation helpers ────────────────────────────

  static DedupEntry? _findExisting(List<DedupEntry> state, String key) {
    for (final entry in state) {
      if (entry.key == key) return entry;
    }
    return null;
  }

  /// Whether the new decision represents a severity escalation over
  /// the existing dedup entry.
  static bool _isEscalation(DedupEntry existing, AlertDecision candidate) {
    return candidate.severity.index > (existing.escalated ? 2 : 1);
  }

  /// Whether the candidate would violate hysteresis — re-alerting for
  /// the same category too soon after a recovery.
  static bool _isHysteresisViolation(
    AlertDecision candidate,
    List<DedupEntry> priorAlerts,
    SensitivityRules rules,
  ) {
    if (candidate.trigger != AlertTrigger.currentThreshold) return false;

    // Check if there's a recent recovery entry for a lower category.
    final recentRecoveries =
        priorAlerts.where((a) => a.key.startsWith('recovery_'));
    for (final recovery in recentRecoveries) {
      if (recovery.key.contains(candidate.dedupKey.split('_').last)) {
        return true;
      }
    }
    return false;
  }

  // ── Severity / guidance helpers ──────────────────────────────────────

  /// The AQI value at which a CPCB category begins (e.g. Poor starts at 201).
  static int _categoryLowerBound(CpcbCategory cat) {
    if (cat == CpcbCategory.good) return 0;
    // Each category starts one above the previous category's upperBound.
    final prevIndex = cat.index - 1;
    return CpcbCategory.values[prevIndex].upperBound + 1;
  }

  /// Alert severity implied by a CPCB category. Public because the forecast
  /// alarm scheduler classifies upcoming crossings the same way the engine
  /// classifies current conditions.
  static AlertSeverity severityForCategory(CpcbCategory cat) {
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
        'Reduce prolonged outdoor exposure. Limit time spent outside '
        'and follow your existing care plan.',
      AlertSeverity.urgent =>
        'Avoid outdoor activity. Stay indoors with windows closed. '
        'Follow your existing care plan.',
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
