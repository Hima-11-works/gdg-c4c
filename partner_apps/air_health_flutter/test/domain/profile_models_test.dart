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
