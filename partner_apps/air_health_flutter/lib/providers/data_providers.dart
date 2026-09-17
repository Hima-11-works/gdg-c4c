import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/pollution_data_provider.dart';
import '../data/providers/dummy_pollution_data_provider.dart';
import '../data/providers/scenario_data.dart';

// When switching to the real API, uncomment these imports:
// import '../data/api_config.dart';
// import '../data/dio_factory.dart';
// import '../data/providers/remote_pollution_data_provider.dart';

/// The single binding point for [PollutionDataProvider].
///
/// To switch to the real API, replace the body with:
/// ```dart
/// final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
///   final config = ApiConfig.fromEnvironment();
///   return RemotePollutionDataProvider(dio: createPollutionDio(config));
/// });
/// ```
///
/// Nothing else in the app changes. Pass env vars via --dart-define:
/// ```
/// flutter run --dart-define=POLLUTION_API_BASE_URL=https://... \
///             --dart-define=POLLUTION_API_KEY=sk-...
/// ```
final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
  return DummyPollutionDataProvider(scenario: Scenario.cleanStable);
});

/// The currently active scenario — only meaningful with the dummy provider.
/// Feature screens can watch this to display scenario info in debug mode.
final activeScenarioProvider = StateProvider<Scenario>((ref) {
  return Scenario.cleanStable;
});
