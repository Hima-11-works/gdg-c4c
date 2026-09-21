import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  group('UserProfile', () {
    test('default values', () {
      const profile = UserProfile();
      expect(profile.healthContext, UserHealthContext.none);
      expect(profile.sensitivity, AlertSensitivity.standard);
      expect(profile.customRules, isNull);
    });

    test('equality', () {
      const a = UserProfile(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.sensitive,
      );
      const b = UserProfile(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.sensitive,
      );
      expect(a, equals(b));
      expect(a.hashCode, equals(b.hashCode));
    });

    test('copyWith', () {
      const original = UserProfile();
      final updated = original.copyWith(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.sensitive,
      );
      expect(updated.healthContext, UserHealthContext.asthma);
      expect(updated.sensitivity, AlertSensitivity.sensitive);
    });

    test('defaults preferences to UserAlertPreferences()', () {
      const profile = UserProfile();
      expect(profile.preferences, const UserAlertPreferences());
    });

    test('copyWith carries preferences', () {
      const original = UserProfile();
      final updated = original.copyWith(
        preferences: const UserAlertPreferences(alertsEnabled: false),
      );
      expect(updated.preferences.alertsEnabled, isFalse);
      expect(original.preferences.alertsEnabled, isTrue); // unchanged
    });

    test('equality includes preferences', () {
      const a = UserProfile(
        preferences: UserAlertPreferences(alertsEnabled: false),
      );
      const b = UserProfile(
        preferences: UserAlertPreferences(alertsEnabled: false),
      );
      const c = UserProfile();
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });
  });

  group('UserAlertPreferences', () {
    test('default values', () {
      const prefs = UserAlertPreferences();
      expect(prefs.alertsEnabled, isTrue);
      expect(prefs.minimumSeverity, AlertSeverity.info);
      expect(prefs.recoveryAlertsEnabled, isTrue);
      expect(prefs.hasQuietHours, isFalse);
    });

    test('equality', () {
      const a = UserAlertPreferences(alertsEnabled: false);
      const b = UserAlertPreferences(alertsEnabled: false);
      expect(a, equals(b));
    });

    test('copyWith', () {
      const original = UserAlertPreferences();
      final updated = original.copyWith(
        minimumSeverity: AlertSeverity.warning,
        recoveryAlertsEnabled: false,
      );
      expect(updated.minimumSeverity, AlertSeverity.warning);
      expect(updated.recoveryAlertsEnabled, isFalse);
      expect(updated.alertsEnabled, isTrue); // unchanged
    });

    test('lead time defaults to null and round-trips', () {
      const prefs = UserAlertPreferences(leadTime: Duration(hours: 6));
      expect(prefs.leadTime, const Duration(hours: 6));
      expect(const UserAlertPreferences().leadTime, isNull);
      expect(prefs.copyWith(clearLeadTime: true).leadTime, isNull);
    });

    test('equality includes lead time', () {
      const a = UserAlertPreferences(leadTime: Duration(hours: 3));
      const b = UserAlertPreferences(leadTime: Duration(hours: 3));
      const c = UserAlertPreferences();
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });

    test('forecast alarms default on, lead defaults to zero', () {
      const prefs = UserAlertPreferences();
      expect(prefs.forecastAlarmsEnabled, isTrue);
      expect(prefs.alarmLead, Duration.zero);
    });

    test('forecast alarm settings round-trip', () {
      const prefs = UserAlertPreferences(
        forecastAlarmsEnabled: false,
        alarmLead: Duration(minutes: 30),
      );
      expect(prefs.copyWith(forecastAlarmsEnabled: true).forecastAlarmsEnabled,
          isTrue);
      expect(
        const UserAlertPreferences()
            .copyWith(alarmLead: const Duration(minutes: 10))
            .alarmLead,
        const Duration(minutes: 10),
      );
      // alarmLead is never null — "no lead" is Duration.zero.
      expect(
        UserAlertPreferences(forecastAlarmsEnabled: false)
            .copyWith(forecastAlarmsEnabled: true)
            .alarmLead,
        Duration.zero,
      );
    });

    test('equality includes forecast alarm settings', () {
      const a = UserAlertPreferences(forecastAlarmsEnabled: false);
      const b = UserAlertPreferences(forecastAlarmsEnabled: false);
      const c = UserAlertPreferences(alarmLead: Duration(minutes: 5));
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });
  });

  group('UserAlertPreferences quiet hours', () {
    final wrapping = UserAlertPreferences(
      quietHoursStart: DateTime(2000, 1, 1, 22, 0),
      quietHoursEnd: DateTime(2000, 1, 1, 7, 0),
    );

    test('detects a window that wraps past midnight', () {
      expect(wrapping.hasQuietHours, isTrue);
      expect(wrapping.isWithinQuietHours(DateTime(2026, 9, 17, 23, 0)), isTrue);
      expect(wrapping.isWithinQuietHours(DateTime(2026, 9, 17, 6, 59)), isTrue);
      expect(wrapping.isWithinQuietHours(DateTime(2026, 9, 17, 7, 0)), isFalse);
      expect(wrapping.isWithinQuietHours(DateTime(2026, 9, 17, 12, 0)), isFalse);
    });

    test('detects a same-day window', () {
      final day = UserAlertPreferences(
        quietHoursStart: DateTime(2000, 1, 1, 9, 0),
        quietHoursEnd: DateTime(2000, 1, 1, 17, 30),
      );
      expect(day.isWithinQuietHours(DateTime(2026, 9, 17, 10, 0)), isTrue);
      expect(day.isWithinQuietHours(DateTime(2026, 9, 17, 8, 59)), isFalse);
      expect(day.isWithinQuietHours(DateTime(2026, 9, 17, 17, 30)), isFalse);
    });

    test('is inactive when unset', () {
      const prefs = UserAlertPreferences();
      expect(prefs.hasQuietHours, isFalse);
      expect(prefs.isWithinQuietHours(DateTime(2026, 9, 17, 23, 0)), isFalse);
    });

    test('copyWith can clear quiet hours', () {
      expect(wrapping.copyWith(clearQuietHours: true).hasQuietHours, isFalse);
    });
  });

  group('UserSensitivityProfile', () {
    test('aggregates context, sensitivity, preferences', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.copd,
        sensitivity: AlertSensitivity.high,
        preferences: UserAlertPreferences(alertsEnabled: true),
      );
      expect(profile.healthContext, UserHealthContext.copd);
      expect(profile.sensitivity, AlertSensitivity.high);
      expect(profile.preferences.alertsEnabled, isTrue);
      expect(profile.hasHealthContext, isTrue);
      expect(profile.isCustom, isFalse);
    });

    test('equality', () {
      const a = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.standard,
        preferences: UserAlertPreferences(),
      );
      const b = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.standard,
        preferences: UserAlertPreferences(),
      );
      expect(a, equals(b));
    });
  });

  group('AlertSensitivity', () {
    test('all values have labels', () {
      for (final s in AlertSensitivity.values) {
        expect(s.label, isNotEmpty);
      }
    });
  });

  group('UserHealthContext', () {
    test('all values have labels', () {
      for (final h in UserHealthContext.values) {
        expect(h.label, isNotEmpty);
      }
    });
  });

  group('AlertSeverity ordering', () {
    test('info < advisory < warning < urgent', () {
      expect(AlertSeverity.info < AlertSeverity.advisory, isTrue);
      expect(AlertSeverity.advisory < AlertSeverity.warning, isTrue);
      expect(AlertSeverity.warning < AlertSeverity.urgent, isTrue);
      expect(AlertSeverity.urgent >= AlertSeverity.warning, isTrue);
    });
  });
}
