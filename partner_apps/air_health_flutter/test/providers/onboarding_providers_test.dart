import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/providers/onboarding_providers.dart';
import 'package:air_health_flutter/providers/prefs_providers.dart';
import 'package:air_health_flutter/providers/profile_providers.dart';
import 'package:air_health_flutter/storage/secure_profile_store.dart';

class MockSecureProfileStore extends Mock implements SecureProfileStore {}

void main() {
  late MockSecureProfileStore store;

  setUp(() {
    store = MockSecureProfileStore();
  });

  Future<bool> complete({
    required bool onboardingFlag,
    UserProfile? profile,
    Object? readError,
  }) async {
    when(() => store.read()).thenAnswer((_) async {
      if (readError != null) throw readError;
      return profile;
    });

    final container = ProviderContainer(
      overrides: [
        onboardingDoneProvider.overrideWith((ref) async => onboardingFlag),
        secureProfileStoreProvider.overrideWithValue(store),
      ],
    );
    addTearDown(container.dispose);
    return container.read(onboardingCompleteProvider.future);
  }

  group('onboardingCompleteProvider', () {
    test('is false when the onboarding flag is not set', () async {
      expect(
        await complete(onboardingFlag: false, profile: const UserProfile()),
        isFalse,
      );
    });

    test('is true when the flag is set AND a profile exists', () async {
      expect(
        await complete(onboardingFlag: true, profile: const UserProfile()),
        isTrue,
      );
    });

    test('is false when the flag is set but no profile exists', () async {
      expect(
        await complete(onboardingFlag: true, profile: null),
        isFalse,
      );
    });

    test('is false when the profile cannot be read (keystore failure)',
        () async {
      expect(
        await complete(
          onboardingFlag: true,
          readError: PlatformException(
            code: 'Exception',
            message: 'javax.crypto.BadPaddingException: BAD_DECRYPT',
          ),
        ),
        isFalse,
      );
    });
  });
}
