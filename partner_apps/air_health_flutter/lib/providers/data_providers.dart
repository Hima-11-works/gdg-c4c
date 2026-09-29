import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_config.dart';
import '../data/dio_factory.dart';
import '../data/grid/grid_api.dart';
import '../data/pollution_data_provider.dart';
import '../data/providers/dummy_pollution_data_provider.dart';
import '../data/providers/grid_api_pollution_data_provider.dart';
import '../data/providers/scenario_data.dart';
import '../data/reports/citizen_sensor_api.dart';
import '../data/reports/fire_report_api.dart';

export '../data/reports/citizen_sensor_api.dart';
export '../data/reports/fire_report_api.dart';

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

/// Fire-report API client, or null when the backend isn't configured.
///
/// Null (dummy mode) means the "report a fire" flow is unavailable - there
/// is nowhere to send it - so the UI hides it entirely rather than showing
/// a button that can only fail.
final fireReportApiClientProvider = Provider<FireReportApiClient?>((ref) {
  final config = ApiConfig.tryFromEnvironment();
  if (config == null) return null;
  return DioFireReportApiClient(dio: createPollutionDio(config));
});

/// Citizen PM2.5 submission client, or null until the API is configured.
final citizenSensorApiClientProvider = Provider<CitizenSensorApiClient?>((ref) {
  final config = ApiConfig.tryFromEnvironment();
  if (config == null) return null;
  return CitizenSensorApiClient(dio: createPollutionDio(config));
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
