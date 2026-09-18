import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/providers/alert_providers.dart';
import 'package:air_health_flutter/providers/profile_providers.dart';
import 'package:air_health_flutter/storage/secure_profile_store.dart';

/// A profile store that reports no stored profile, so the coordinator's
/// "no profile yet" guard can be exercised without touching plugins.
class _EmptyProfileStore extends SecureProfileStore {
  @override
  Future<UserProfile?> read() async => null;
}

void main() {
  group('AlertCoordinator', () {
    test('refreshAndEvaluate returns null when there is no profile', () async {
      final container = ProviderContainer(
        overrides: [
          secureProfileStoreProvider.overrideWithValue(_EmptyProfileStore()),
        ],
      );
      addTearDown(container.dispose);

      final result =
          await container.read(alertCoordinatorProvider).refreshAndEvaluate();

      // No profile -> no evaluation, no notification, no history.
      expect(result, isNull);
    });
  });
}
