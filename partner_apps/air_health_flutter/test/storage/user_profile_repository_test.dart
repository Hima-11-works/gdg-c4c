import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/storage/secure_profile_store.dart';
import 'package:air_health_flutter/storage/user_profile_repository.dart';

class MockSecureProfileStore extends Mock implements SecureProfileStore {}

void main() {
  late MockSecureProfileStore mockStore;
  late UserProfileRepository repo;

  setUpAll(() {
    registerFallbackValue(const UserProfile());
  });

  setUp(() {
    mockStore = MockSecureProfileStore();
    repo = UserProfileRepository(store: mockStore);
  });

  group('UserProfileRepository', () {
    group('loadProfile', () {
      test('returns profile when one exists', () async {
        const profile = UserProfile(
          healthContext: UserHealthContext.asthma,
          sensitivity: AlertSensitivity.sensitive,
        );
        when(() => mockStore.read()).thenAnswer((_) async => profile);

        final result = await repo.loadProfile();
        expect(result, equals(profile));
        verify(() => mockStore.read()).called(1);
      });

      test('returns null when no profile exists', () async {
        when(() => mockStore.read()).thenAnswer((_) async => null);

        final result = await repo.loadProfile();
        expect(result, isNull);
      });
    });

    group('saveProfile', () {
      test('saves profile to store', () async {
        const profile = UserProfile(
          healthContext: UserHealthContext.copd,
          sensitivity: AlertSensitivity.high,
        );
        when(() => mockStore.save(profile)).thenAnswer((_) async {});

        await repo.saveProfile(profile);
        verify(() => mockStore.save(profile)).called(1);
      });
    });

    group('updateProfile', () {
      test('updates existing profile fields', () async {
        const existing = UserProfile(
          healthContext: UserHealthContext.none,
          sensitivity: AlertSensitivity.standard,
        );
        when(() => mockStore.read()).thenAnswer((_) async => existing);
        when(() => mockStore.save(any())).thenAnswer((_) async {});

        final result = await repo.updateProfile(
          sensitivity: AlertSensitivity.sensitive,
        );

        expect(result.healthContext, UserHealthContext.none);
        expect(result.sensitivity, AlertSensitivity.sensitive);
        verify(() => mockStore.save(any())).called(1);
      });

      test('creates new profile when none exists', () async {
        when(() => mockStore.read()).thenAnswer((_) async => null);
        when(() => mockStore.save(any())).thenAnswer((_) async {});

        final result = await repo.updateProfile(
          healthContext: UserHealthContext.asthma,
        );

        expect(result.healthContext, UserHealthContext.asthma);
        expect(result.sensitivity, AlertSensitivity.standard);
      });

      test('preserves existing fields not being updated', () async {
        const existing = UserProfile(
          healthContext: UserHealthContext.asthma,
          sensitivity: AlertSensitivity.high,
        );
        when(() => mockStore.read()).thenAnswer((_) async => existing);
        when(() => mockStore.save(any())).thenAnswer((_) async {});

        final result = await repo.updateProfile(
          healthContext: UserHealthContext.copd,
        );

        expect(result.healthContext, UserHealthContext.copd);
        expect(result.sensitivity, AlertSensitivity.high);
      });
    });

    group('deleteProfile', () {
      test('deletes profile from store', () async {
        when(() => mockStore.delete()).thenAnswer((_) async {});

        await repo.deleteProfile();
        verify(() => mockStore.delete()).called(1);
      });
    });

    group('resetProfile', () {
      test('saves default profile and returns it', () async {
        when(() => mockStore.save(any())).thenAnswer((_) async {});

        final result = await repo.resetProfile();

        expect(result, const UserProfile());
        expect(result.healthContext, UserHealthContext.none);
        expect(result.sensitivity, AlertSensitivity.standard);

        final saved = verify(() => mockStore.save(captureAny())).captured;
        expect(saved.single, const UserProfile());
      });
    });
  });
}
