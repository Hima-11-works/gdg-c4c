import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/alert_providers.dart';
import '../providers/data_providers.dart';
import 'dev_scenario_simulator.dart';

/// Dev-only provider overrides.
///
/// In debug builds with [devScenarioSimulatorEnabled], the simulator's
/// [DummyPollutionDataProvider] replaces [pollutionDataProvider] and the alert
/// dedup state can be reset via [alertDedupResetProvider].
///
/// In release builds, these overrides are empty — the standard
/// providers are used unchanged.
List<Override> get devProviderOverrides {
  if (!kDebugMode || !devScenarioSimulatorEnabled) return const [];

  return [
    // Override the data provider to use the simulator's time-shifted one.
    pollutionDataProvider.overrideWith((ref) {
      return ref.watch(simulatorDataProvider);
    }),
    // Wire the dedup reset.
    alertDedupResetProvider.overrideWith((ref) {
      return () {
        ref.read(alertDedupStateProvider.notifier).state = [];
      };
    }),
  ];
}
