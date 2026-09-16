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

    group('units', () {
      test('defaults to metric', () async {
        expect(await store.getUnits(), 'metric');
      });

      test('persists units', () async {
        await store.setUnits('imperial');
        expect(await store.getUnits(), 'imperial');
      });
    });

    group('dark mode', () {
      test('defaults to false', () async {
        expect(await store.getDarkMode(), isFalse);
      });

      test('persists dark mode', () async {
        await store.setDarkMode(true);
        expect(await store.getDarkMode(), isTrue);
      });
    });

    group('reduced motion', () {
      test('defaults to false', () async {
        expect(await store.getReducedMotion(), isFalse);
      });

      test('persists reduced motion', () async {
        await store.setReducedMotion(true);
        expect(await store.getReducedMotion(), isTrue);
      });
    });

    group('resetAll', () {
      test('clears all preferences', () async {
        await store.setOnboardingDone(true);
        await store.setUnits('imperial');
        await store.setDarkMode(true);
        await store.setReducedMotion(true);

        await store.resetAll();

        expect(await store.getOnboardingDone(), isFalse);
        expect(await store.getUnits(), 'metric');
        expect(await store.getDarkMode(), isFalse);
        expect(await store.getReducedMotion(), isFalse);
      });
    });
  });
}
