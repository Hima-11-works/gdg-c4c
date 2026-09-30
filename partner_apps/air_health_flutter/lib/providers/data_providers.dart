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

/// Grid API client; defaults to the deployed Vercel backend in every build mode.
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

/// Fire-report API client; the deployed Vercel backend is used by default.
///
/// A null override (used by offline tests) means the "report a fire" flow is
/// unavailable — there is nowhere to send it — so the UI hides the entry point.
final fireReportApiClientProvider = Provider<FireReportApiClient?>((ref) {
  final config = ApiConfig.tryFromEnvironment();
  if (config == null) return null;
  return DioFireReportApiClient(dio: createPollutionDio(config));
});

/// Current backend review lifecycle for a locally submitted citizen report.
final citizenReportReviewStatusProvider =
    FutureProvider.family<ReportReviewStatusDto?, int>((ref, reportId) async {
  final client = ref.watch(fireReportApiClientProvider);
  if (client is CitizenReportStatusApiClient) {
    return (client as CitizenReportStatusApiClient).getReviewStatus(reportId);
  }
  return null;
});

/// Citizen PM2.5 submission client; defaults to the deployed Vercel backend.
final citizenSensorApiClientProvider = Provider<CitizenSensorApiClient?>((ref) {
  final config = ApiConfig.tryFromEnvironment();
  if (config == null) return null;
  return CitizenSensorApiClient(dio: createPollutionDio(config));
});

/// The single binding point for [PollutionDataProvider].
///
/// The app talks to the deployed Vercel grid API by default through
/// [GridApiPollutionDataProvider]. Set `POLLUTION_API_BASE_URL` to target a
/// local or staging backend. A null client override falls back to deterministic
/// dummy data, which keeps offline tests isolated. Debug scenario
/// simulation only replaces the selected source when explicitly enabled with
/// `USE_DEV_SCENARIO_SIMULATOR=true`.
///
/// Nothing else in the app changes between the two.
final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
  final client = ref.watch(gridApiClientProvider);
  if (client != null) {
    return GridApiPollutionDataProvider(client: client);
  }
  return DummyPollutionDataProvider(scenario: Scenario.cleanStable);
});
