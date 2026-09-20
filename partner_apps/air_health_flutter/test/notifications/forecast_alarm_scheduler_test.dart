import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/domain/sensitivity_rules.dart';
import 'package:air_health_flutter/notifications/forecast_alarm_scheduler.dart';
import 'package:air_health_flutter/notifications/notification_service.dart';
import 'package:air_health_flutter/notifications/scheduled_alarm.dart';
import 'package:air_health_flutter/storage/forecast_alarm_store.dart';

class MockNotificationService extends Mock implements NotificationService {}

class MockForecastAlarmStore extends Mock implements ForecastAlarmStore {}

final now = DateTime(2026, 9, 21, 10, 0);

DataFreshness fresh(DateTime at) =>
    DataFreshness(retrievedAt: at, quality: DataQuality.full);

/// A forecast point `ahead` hours from [now].
ForecastPoint fp(int aqi, Duration ahead, {double confidence = 0.9}) =>
    ForecastPoint(at: now.add(ahead), aqiCpcb: aqi, confidence: confidence);

UserSensitivityProfile profile(AlertSensitivity sensitivity) =>
    UserSensitivityProfile(
      healthContext: UserHealthContext.none,
      sensitivity: sensitivity,
      preferences: const UserAlertPreferences(),
    );

final standardRules = SensitivityRules.forProfile(
  profile(AlertSensitivity.standard),
); // warns at Poor, confidence >= 0.8
final sensitiveRules = SensitivityRules.forProfile(
  profile(AlertSensitivity.sensitive),
); // warns at Moderate, confidence >= 0.7

void main() {
  setUpAll(() {
    registerFallbackValue(const UserAlertPreferences());
    registerFallbackValue(<ScheduledAlarm>[]);
    registerFallbackValue(DateTime(2026, 9, 21));
  });

  group('desiredAlarms (pure selection)', () {
    test('schedules the nearest qualifying crossing', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [
          fp(180, const Duration(hours: 1)), // below Poor: skipped
          fp(250, const Duration(hours: 3)),
          fp(280, const Duration(hours: 4)),
        ],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now),
        now: now,
      );

      expect(desired, hasLength(2));
      // alarmLead defaults to zero: the alarm rings at the crossing itself.
      expect(desired.first.fireAt, now.add(const Duration(hours: 3)));
      expect(desired.first.key,
          ForecastAlarmScheduler.keyFor(CpcbCategory.poor, now.add(const Duration(hours: 3))));
      expect(desired.first.severity, AlertSeverity.warning);
      expect(desired.last.fireAt, now.add(const Duration(hours: 4)));
      // Sorted oldest first.
      expect(
        desired.first.fireAt.isBefore(desired.last.fireAt),
        isTrue,
      );
    });

    test('skips low-confidence crossings', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(250, const Duration(hours: 3), confidence: 0.5)],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now),
        now: now,
      );
      expect(desired, isEmpty);
    });

    test('skips crossings beyond the forecast horizon', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(250, const Duration(hours: 7))],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now),
        now: now,
      );
      expect(desired, isEmpty);
    });

    test('skips imminent crossings (the in-app heads-up covers those)', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(250, const Duration(minutes: 1))],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now),
        now: now,
      );
      expect(desired, isEmpty);
    });

    test('ignores stale data entirely', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(250, const Duration(hours: 3))],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: DataFreshness(retrievedAt: now, quality: DataQuality.stale),
        now: now,
      );
      expect(desired, isEmpty);
    });

    test('ignores data older than two hours', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(250, const Duration(hours: 3))],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now.subtract(const Duration(hours: 3))),
        now: now,
      );
      expect(desired, isEmpty);
    });

    test('honours the master alert switch and the severity floor', () {
      // minimumSeverity = warning filters a Moderate (advisory) crossing.
      final advisory = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(150, const Duration(hours: 2))],
        rules: sensitiveRules, // forecastCategory = Moderate
        preferences:
            const UserAlertPreferences(minimumSeverity: AlertSeverity.warning),
        freshness: fresh(now),
        now: now,
      );
      expect(advisory, isEmpty);

      // Same crossing with the default floor is allowed.
      final allowed = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(150, const Duration(hours: 2))],
        rules: sensitiveRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now),
        now: now,
      );
      expect(allowed, hasLength(1));
      expect(allowed.single.severity, AlertSeverity.advisory);
    });

    test('alarmLead shifts the ring time before the crossing', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [fp(250, const Duration(hours: 3))],
        rules: standardRules,
        preferences:
            const UserAlertPreferences(alarmLead: Duration(minutes: 30)),
        freshness: fresh(now),
        now: now,
      );
      expect(desired.single.fireAt,
          now.add(const Duration(hours: 3)).subtract(const Duration(minutes: 30)));
    });

    test('keeps at most the nearest three alarms', () {
      final desired = ForecastAlarmScheduler.desiredAlarms(
        forecast: [
          fp(250, const Duration(hours: 3)),
          fp(260, const Duration(hours: 4)),
          fp(270, const Duration(hours: 5)),
          fp(280, const Duration(hours: 5, minutes: 30)),
        ],
        rules: standardRules,
        preferences: const UserAlertPreferences(),
        freshness: fresh(now),
        now: now,
      );
      expect(desired, hasLength(ForecastAlarmScheduler.maxScheduledAlarms));
    });
  });

  group('keyFor / idFor', () {
    test('same crossing produces the same key', () {
      final at = DateTime(2026, 9, 21, 13, 30);
      expect(ForecastAlarmScheduler.keyFor(CpcbCategory.poor, at),
          ForecastAlarmScheduler.keyFor(CpcbCategory.poor, at));
    });

    test('different crossing (hour) produces a different key', () {
      final a = DateTime(2026, 9, 21, 13, 0);
      final b = DateTime(2026, 9, 21, 14, 0);
      expect(ForecastAlarmScheduler.keyFor(CpcbCategory.poor, a),
          isNot(ForecastAlarmScheduler.keyFor(CpcbCategory.poor, b)));
    });

    test('alarm ids live in the alarm range and are deterministic', () {
      final key = ForecastAlarmScheduler.keyFor(
          CpcbCategory.poor, DateTime(2026, 9, 21, 13, 0));
      final id = ForecastAlarmScheduler.idFor(key);
      expect(ForecastAlarmScheduler.idFor(key), id);
      expect(id >= 0x40000000, isTrue);
      expect(id < 0x80000000, isTrue);
    });
  });

  group('reconcile', () {
    late MockNotificationService service;
    late MockForecastAlarmStore store;
    late ForecastAlarmScheduler scheduler;

    setUp(() {
      service = MockNotificationService();
      store = MockForecastAlarmStore();
      scheduler = ForecastAlarmScheduler(
        notificationService: service,
        store: store,
      );
      when(() => service.cancelAlarm(any())).thenAnswer((_) async {});
      when(() => service.scheduleAlarm(
            id: any(named: 'id'),
            fireAt: any(named: 'fireAt'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          )).thenAnswer((_) async {});
    });

    Future<List<ScheduledAlarm>> run(
      List<ForecastPoint> forecast, {
      List<ScheduledAlarm> stored = const [],
      UserAlertPreferences preferences = const UserAlertPreferences(),
    }) {
      when(() => store.readAll()).thenAnswer((_) async => stored);
      return scheduler.reconcile(
        forecast: forecast,
        rules: standardRules,
        preferences: preferences,
        freshness: fresh(now),
        now: now,
      );
    }

    test('schedules a new alarm and persists it', () async {
      final result = await run([fp(250, const Duration(hours: 3))]);

      expect(result, hasLength(1));
      verify(() => service.scheduleAlarm(
            id: any(named: 'id'),
            fireAt: any(named: 'fireAt'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          )).called(1);
      verify(() => store.writeAll(any())).called(1);
    });

    test('an unchanged alarm is kept without re-registering it', () async {
      final at = now.add(const Duration(hours: 3));
      final key = ForecastAlarmScheduler.keyFor(CpcbCategory.poor, at);
      final stored = ScheduledAlarm(
        id: ForecastAlarmScheduler.idFor(key),
        key: key,
        fireAt: at,
        severity: AlertSeverity.warning,
        categoryLabel: 'Poor',
        predictedAqi: 250,
      );

      final result = await run([fp(250, const Duration(hours: 3))],
          stored: [stored]);

      expect(result, equals([stored]));
      verifyNever(() => service.scheduleAlarm(
            id: any(named: 'id'),
            fireAt: any(named: 'fireAt'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          ));
    });

    test('cancels an alarm whose crossing no longer qualifies', () async {
      final key =
          ForecastAlarmScheduler.keyFor(CpcbCategory.poor, now.add(const Duration(hours: 3)));

      final result = await run([fp(180, const Duration(hours: 3))],
          stored: [
            ScheduledAlarm(
              id: ForecastAlarmScheduler.idFor(key),
              key: key,
              fireAt: now.add(const Duration(hours: 3)),
              severity: AlertSeverity.warning,
              categoryLabel: 'Poor',
              predictedAqi: 250,
            ),
          ]);

      expect(result, isEmpty);
      verify(() => service.cancelAlarm(ForecastAlarmScheduler.idFor(key)))
          .called(1);
    });

    test('a moved crossing is cancelled and rescheduled', () async {
      final oldKey = ForecastAlarmScheduler.keyFor(
          CpcbCategory.poor, now.add(const Duration(hours: 3)));

      final result = await run([fp(250, const Duration(hours: 4))],
          stored: [
            ScheduledAlarm(
              id: ForecastAlarmScheduler.idFor(oldKey),
              key: oldKey,
              fireAt: now.add(const Duration(hours: 3)),
              severity: AlertSeverity.warning,
              categoryLabel: 'Poor',
              predictedAqi: 250,
            ),
          ]);

      // Old alarm cancelled, new alarm scheduled for the revised crossing.
      verify(() => service.cancelAlarm(ForecastAlarmScheduler.idFor(oldKey)))
          .called(1);
      expect(result, hasLength(1));
      expect(result.single.fireAt, now.add(const Duration(hours: 4)));
      expect(
        result.single.key,
        ForecastAlarmScheduler.keyFor(CpcbCategory.poor, now.add(const Duration(hours: 4))),
      );
    });

    test('already-fired alarms are dropped without cancelling', () async {
      final key = ForecastAlarmScheduler.keyFor(
          CpcbCategory.poor, now.subtract(const Duration(hours: 1)));
      final fired = ScheduledAlarm(
        id: ForecastAlarmScheduler.idFor(key),
        key: key,
        fireAt: now.subtract(const Duration(minutes: 5)),
        severity: AlertSeverity.warning,
        categoryLabel: 'Poor',
        predictedAqi: 250,
      );

      final result = await run(const [], stored: [fired]);

      expect(result, isEmpty);
      verifyNever(() => service.cancelAlarm(any()));
      verify(() => store.writeAll(any())).called(1);
    });

    test('turning alarms off cancels everything', () async {
      final key = ForecastAlarmScheduler.keyFor(
          CpcbCategory.poor, now.add(const Duration(hours: 3)));
      final stored = ScheduledAlarm(
        id: ForecastAlarmScheduler.idFor(key),
        key: key,
        fireAt: now.add(const Duration(hours: 3)),
        severity: AlertSeverity.warning,
        categoryLabel: 'Poor',
        predictedAqi: 250,
      );

      final result = await run(
        [fp(250, const Duration(hours: 3))],
        stored: [stored],
        preferences: const UserAlertPreferences(forecastAlarmsEnabled: false),
      );

      expect(result, isEmpty);
      verify(() => service.cancelAlarm(ForecastAlarmScheduler.idFor(key)))
          .called(1);
    });
  });
}
