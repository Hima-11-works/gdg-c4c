import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/notifications/notification_service.dart';
import 'package:air_health_flutter/providers/alert_providers.dart';
import 'package:air_health_flutter/providers/profile_providers.dart';
import 'package:air_health_flutter/storage/forecast_alarm_store.dart';
import 'package:air_health_flutter/storage/secure_profile_store.dart';

/// A profile store that reports no stored profile, so the coordinator's
/// "no profile yet" guard can be exercised without touching plugins.
class _EmptyProfileStore extends SecureProfileStore {
  @override
  Future<UserProfile?> read() async => null;
}

class _MockNotificationService extends Mock implements NotificationService {}

class _MockForecastAlarmStore extends Mock implements ForecastAlarmStore {}

void main() {
  group('AlertCoordinator', () {
    test('refreshAndEvaluate returns null when there is no profile', () async {
      final store = _MockForecastAlarmStore();
      final container = ProviderContainer(
        overrides: [
          secureProfileStoreProvider.overrideWithValue(_EmptyProfileStore()),
          notificationServiceProvider.overrideWithValue(_MockNotificationService()),
          forecastAlarmStoreProvider.overrideWithValue(store),
        ],
      );
      addTearDown(container.dispose);
      // The no-profile path cancels left-over alarms; nothing to cancel.
      when(() => store.readAll()).thenAnswer((_) async => const []);
      when(() => store.deleteAll()).thenAnswer((_) async {});

      final result =
          await container.read(alertCoordinatorProvider).refreshAndEvaluate();

      // No profile -> no evaluation, no notification, no history.
      expect(result, isNull);
    });

    test('cancels OS-level alarms when there is no profile', () async {
      final store = _MockForecastAlarmStore();
      final service = _MockNotificationService();
      final container = ProviderContainer(
        overrides: [
          secureProfileStoreProvider.overrideWithValue(_EmptyProfileStore()),
          notificationServiceProvider.overrideWithValue(service),
          forecastAlarmStoreProvider.overrideWithValue(store),
        ],
      );
      addTearDown(container.dispose);
      when(() => store.readAll()).thenAnswer((_) async => const []);
      when(() => store.deleteAll()).thenAnswer((_) async {});

      await container.read(alertCoordinatorProvider).refreshAndEvaluate();

      // Left-over alarms from a previous state must not keep ringing.
      verify(() => store.deleteAll()).called(1);
      verifyNever(() => service.scheduleAlarm(
            id: any(named: 'id'),
            fireAt: any(named: 'fireAt'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          ));
    });
  });
}
