import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_config.dart';
import '../data/dio_factory.dart';
import '../data/grid/grid_api.dart';
import '../data/pollution_data_provider.dart';
import '../data/providers/dummy_pollution_data_provider.dart';
import '../data/providers/grid_api_pollution_data_provider.dart';
import '../data/providers/scenario_data.dart';

/// Grid API client, or null when `POLLUTION_API_BASE_URL` is not set.
///
/// The grid API is unauthenticated, so only the base URL is required:
/// ```
/// flutter run --dart-define=POLLUTION_API_BASE_URL=http://localhost:8000
/// ```
final gridApiClientProvider = Provider<GridApiClient?>((ref) {
  final config = ApiConfig.tryFromEnvironment();
  if (config == null) return null;
  return DioGridApiClient(dio: createPollutionDio(config));
});

/// The single binding point for [PollutionDataProvider].
///
/// With `POLLUTION_API_BASE_URL` set, the app talks to the real grid API
/// through [GridApiPollutionDataProvider]; otherwise it falls back to the
/// deterministic dummy data so the app runs with no backend. In debug builds
/// `devProviderOverrides` swaps in the scenario simulator regardless.
///
/// Nothing else in the app changes between the two.
final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
  final client = ref.watch(gridApiClientProvider);
  if (client != null) {
    return GridApiPollutionDataProvider(client: client);
  }
  return DummyPollutionDataProvider(scenario: Scenario.cleanStable);
});

/// The currently active scenario — only meaningful with the dummy provider.
/// Feature screens can watch this to display scenario info in debug mode.
final activeScenarioProvider = StateProvider<Scenario>((ref) {
  return Scenario.cleanStable;
});
