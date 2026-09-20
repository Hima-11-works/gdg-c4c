import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/storage/secure_profile_store.dart';

class MockFlutterSecureStorage extends Mock implements FlutterSecureStorage {}

void main() {
  late MockFlutterSecureStorage storage;
  late SecureProfileStore store;

  setUp(() {
    storage = MockFlutterSecureStorage();
    store = SecureProfileStore(storage: storage);
  });

  test('round-trips custom rules, quiet hours and lead time', () async {
    String? saved;
    when(() => storage.write(
          key: any(named: 'key'),
          value: any(named: 'value'),
        )).thenAnswer((invocation) async {
      saved = invocation.namedArguments[#value] as String?;
    });
    when(() => storage.read(key: any(named: 'key')))
        .thenAnswer((_) async => saved);

    final profile = UserProfile(
      sensitivity: AlertSensitivity.custom,
      customRules: const CustomSensitivityRules(
        warningAqi: 80,
        forecastWarningAqi: 120,
        rapidRiseAqiPerHour: 20,
      ),
      preferences: UserAlertPreferences(
        minimumSeverity: AlertSeverity.warning,
        quietHoursStart: DateTime(2000, 1, 1, 22, 0),
        quietHoursEnd: DateTime(2000, 1, 1, 7, 0),
        leadTime: const Duration(hours: 6),
        forecastAlarmsEnabled: false,
        alarmLead: const Duration(minutes: 15),
      ),
    );

    await store.save(profile);
    final loaded = await store.read();

    expect(loaded, isNotNull);
    expect(loaded!.sensitivity, AlertSensitivity.custom);
    expect(loaded.customRules, profile.customRules);
    expect(loaded.preferences.minimumSeverity, AlertSeverity.warning);
    expect(loaded.preferences.quietHoursStart?.hour, 22);
    expect(loaded.preferences.quietHoursEnd?.hour, 7);
    expect(loaded.preferences.leadTime, const Duration(hours: 6));
    expect(loaded.preferences.forecastAlarmsEnabled, isFalse);
    expect(loaded.preferences.alarmLead, const Duration(minutes: 15));
  });

  test('reads a legacy payload without custom rules or lead time', () async {
    when(() => storage.read(key: any(named: 'key'))).thenAnswer(
      (_) async => '{"healthContext":"asthma","sensitivity":"sensitive",'
          '"preferences":{"alertsEnabled":true}}',
    );

    final loaded = await store.read();

    expect(loaded, isNotNull);
    expect(loaded!.healthContext, UserHealthContext.asthma);
    expect(loaded.customRules, isNull);
    expect(loaded.preferences.leadTime, isNull);
    expect(loaded.preferences.hasQuietHours, isFalse);
    expect(loaded.preferences.forecastAlarmsEnabled, isTrue);
    expect(loaded.preferences.alarmLead, Duration.zero);
  });

  test('keystore decryption failure returns null instead of throwing',
      () async {
    // Simulates the Android BadPaddingException / BAD_DECRYPT case: the
    // keystore key was invalidated (reinstall, restore-from-backup, OS
    // upgrade) so the stored value cannot be decrypted.
    when(() => storage.read(key: any(named: 'key'))).thenThrow(
      PlatformException(
        code: 'Exception',
        message: 'javax.crypto.BadPaddingException: BAD_DECRYPT',
      ),
    );
    when(() => storage.delete(key: any(named: 'key')))
        .thenAnswer((_) async {});

    final loaded = await store.read();

    expect(loaded, isNull);
    verify(() => storage.delete(key: any(named: 'key'))).called(1);
  });

  test('corrupt stored value returns null and clears it', () async {
    when(() => storage.read(key: any(named: 'key')))
        .thenAnswer((_) async => 'not json');
    when(() => storage.delete(key: any(named: 'key')))
        .thenAnswer((_) async {});

    final loaded = await store.read();

    expect(loaded, isNull);
    verify(() => storage.delete(key: any(named: 'key'))).called(1);
  });

  test('delete failure is swallowed so read still returns null', () async {
    when(() => storage.read(key: any(named: 'key')))
        .thenThrow(PlatformException(code: 'Exception'));
    when(() => storage.delete(key: any(named: 'key')))
        .thenThrow(PlatformException(code: 'Exception'));

    final loaded = await store.read();

    expect(loaded, isNull);
  });
}
