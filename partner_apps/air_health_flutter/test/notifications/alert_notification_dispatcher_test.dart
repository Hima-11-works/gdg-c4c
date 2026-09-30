import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:air_health_flutter/domain/alert_message_service.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/notifications/alert_notification_dispatcher.dart';
import 'package:air_health_flutter/notifications/notification_service.dart';

class MockNotificationService extends Mock implements NotificationService {}

void main() {
  late MockNotificationService mockNotif;
  late AlertMessageService messageService;
  late AlertNotificationDispatcher dispatcher;

  setUp(() {
    mockNotif = MockNotificationService();
    when(() => mockNotif.canDeliverNotifications())
        .thenAnswer((_) async => true);
    messageService = const AlertMessageService();
    dispatcher = AlertNotificationDispatcher(
      notificationService: mockNotif,
      messageService: messageService,
    );
  });

  AlertDecision makeDecision({
    AlertTrigger trigger = AlertTrigger.currentThreshold,
    AlertSeverity severity = AlertSeverity.warning,
    int currentAqi = 250,
    String dedupKey = 'test_key',
  }) {
    return AlertDecision(
      shouldAlert: true,
      severity: severity,
      trigger: trigger,
      currentAqi: currentAqi,
      confidence: 0.85,
      messageContext: '',
      dedupKey: dedupKey,
      guidance: '',
    );
  }

  group('AlertNotificationDispatcher', () {
    test('does not dispatch when OS notification permission is denied', () async {
      when(() => mockNotif.canDeliverNotifications())
          .thenAnswer((_) async => false);

      final count = await dispatcher.dispatch(
        decisions: [makeDecision()],
        sensitivity: AlertSensitivity.standard,
      );

      expect(count, 0);
      verifyNever(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          ));
    });

    test('dispatches notification for each decision', () async {
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((_) async {});

      final decisions = [
        makeDecision(dedupKey: 'key1'),
        makeDecision(dedupKey: 'key2'),
      ];

      final count = await dispatcher.dispatch(
        decisions: decisions,
        sensitivity: AlertSensitivity.standard,
      );

      expect(count, 2);
      verify(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).called(2);
    });

    test('uses lock-screen body, not full body', () async {
      String? capturedBody;
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((invocation) async {
        capturedBody = invocation.namedArguments[#body] as String;
      });

      await dispatcher.dispatch(
        decisions: [makeDecision()],
        sensitivity: AlertSensitivity.sensitive,
      );

      // The lock-screen body should NOT contain "early" or "sensitive".
      expect(capturedBody, isNot(contains('early')));
      expect(capturedBody, isNot(contains('sensitive')));
    });

    test('skips decisions where shouldAlert is false', () async {
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((_) async {});

      final decisions = [
        AlertDecision.none, // shouldAlert = false
      ];

      final count = await dispatcher.dispatch(
        decisions: decisions,
        sensitivity: AlertSensitivity.standard,
      );

      expect(count, 0);
      verifyNever(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          ));
    });

    test('deduplicates same dedupKey within a dispatch call', () async {
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((_) async {});

      final decisions = [
        makeDecision(dedupKey: 'same_key'),
        makeDecision(dedupKey: 'same_key'),
      ];

      final count = await dispatcher.dispatch(
        decisions: decisions,
        sensitivity: AlertSensitivity.standard,
      );

      // Only 1 notification despite 2 decisions with same key.
      expect(count, 1);
      verify(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).called(1);
    });

    test('marks urgent-severity alerts as urgent', () async {
      bool? capturedUrgent;
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          )).thenAnswer((invocation) async {
        capturedUrgent = invocation.namedArguments[#urgent] as bool?;
      });

      await dispatcher.dispatch(
        decisions: [makeDecision(severity: AlertSeverity.urgent)],
        sensitivity: AlertSensitivity.standard,
      );

      expect(capturedUrgent, isTrue);
    });

    test('same dedupKey produces same notification ID', () async {
      final ids = <int>[];
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((invocation) async {
        ids.add(invocation.namedArguments[#id] as int);
      });

      await dispatcher.dispatch(
        decisions: [makeDecision(dedupKey: 'key_a')],
        sensitivity: AlertSensitivity.standard,
      );
      await dispatcher.dispatch(
        decisions: [makeDecision(dedupKey: 'key_a')],
        sensitivity: AlertSensitivity.standard,
      );

      // Both calls should produce the same notification ID.
      expect(ids.length, 2);
      expect(ids[0], ids[1]);
    });

    test('cancelAll clears active notifications', () async {
      when(() => mockNotif.cancelAll()).thenAnswer((_) async {});

      await dispatcher.cancelAll();
      verify(() => mockNotif.cancelAll()).called(1);
    });

    test('cancelByKey cancels specific notification', () async {
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((_) async {});
      when(() => mockNotif.cancel(any())).thenAnswer((_) async {});

      // First dispatch to register the key.
      await dispatcher.dispatch(
        decisions: [makeDecision(dedupKey: 'cancel_me')],
        sensitivity: AlertSensitivity.standard,
      );

      await dispatcher.cancelByKey('cancel_me');
      verify(() => mockNotif.cancel(any())).called(1);
    });

    test('no diagnostic language in any notification text', () async {
      final capturedBodies = <String>[];
      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
          )).thenAnswer((invocation) async {
        capturedBodies.add(invocation.namedArguments[#body] as String);
      });

      for (final trigger in AlertTrigger.values) {
        for (final sensitivity in AlertSensitivity.values) {
          await dispatcher.dispatch(
            decisions: [makeDecision(trigger: trigger)],
            sensitivity: sensitivity,
          );
        }
      }

      const banned = [
        'attack', 'medication', 'medicine', 'prescription',
        'diagnosis', 'diagnose', 'asthma', 'copd',
        'safe for', 'take your', 'prescribed', 'treatments',
        'seek medical',
      ];

      for (final body in capturedBodies) {
        final lower = body.toLowerCase();
        for (final word in banned) {
          expect(lower, isNot(contains(word)),
              reason: 'Found "$word" in notification body: $body');
        }
      }
    });
  });
}
