import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/pollution_data_provider.dart';
import '../data/providers/dummy_pollution_data_provider.dart';
import '../data/providers/scenario_data.dart';

/// The single binding point for [PollutionDataProvider].
///
/// To switch to a real API later, change ONE override here —
/// no screen, widget, alert engine, or chart needs to know.
/// Default: [DummyPollutionDataProvider] with [Scenario.cleanStable].
final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
  return DummyPollutionDataProvider(scenario: Scenario.cleanStable);
});

/// The currently active scenario — only meaningful with the dummy provider.
/// Feature screens can watch this to display scenario info in debug mode.
final activeScenarioProvider = StateProvider<Scenario>((ref) {
  return Scenario.cleanStable;
});
