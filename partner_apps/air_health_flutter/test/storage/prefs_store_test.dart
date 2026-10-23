import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:air_health_flutter/storage/prefs_store.dart';

void main() {
  group('PrefsStore', () {
    late PrefsStore store;

    setUp(() async {
      SharedPreferences.setMockInitialValues({});
      store = PrefsStore();
    });

    group('onboarding', () {
      test('defaults to false', () async {
        expect(await store.getOnboardingDone(), isFalse);
      });

      test('persists onboarding done', () async {
        await store.setOnboardingDone(true);
        expect(await store.getOnboardingDone(), isTrue);
      });
    });

    group('app language', () {
      test('defaults to English and persists a supported locale', () async {
        expect(await store.getAppLanguage(), 'en');
        await store.setAppLanguage('hi');
        expect(await store.getAppLanguage(), 'hi');
      });

      test('rejects unsupported locales', () async {
        await expectLater(store.setAppLanguage('xx'), throwsArgumentError);
      });
    });

    group('resetAll', () {
      test('clears onboarding and any legacy keys', () async {
        SharedPreferences.setMockInitialValues({
          'onboarding_done': true,
          'units': 'imperial',
          'dark_mode': true,
          'reduced_motion': true,
          'app_language': 'pa',
        });
        final seeded = PrefsStore();

        await seeded.resetAll();

        expect(await seeded.getOnboardingDone(), isFalse);
        final prefs = await SharedPreferences.getInstance();
        expect(prefs.getString('units'), isNull);
        expect(prefs.getBool('dark_mode'), isNull);
        expect(prefs.getBool('reduced_motion'), isNull);
        expect(await seeded.getAppLanguage(), 'en');
      });
    });
  });
}
