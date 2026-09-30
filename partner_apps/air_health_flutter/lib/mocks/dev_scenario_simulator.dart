import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/providers/dummy_pollution_data_provider.dart';
import '../data/providers/scenario_data.dart';

/// Synthetic scenario mode is explicit opt-in, so a configured live backend
/// is not silently replaced during an ordinary debug run.
const devScenarioSimulatorEnabled = bool.fromEnvironment(
  'USE_DEV_SCENARIO_SIMULATOR',
  defaultValue: false,
);

/// Time offset for the scenario simulator — how far "now" has advanced
/// from the initial anchor.
final simulatorTimeOffsetProvider =
    StateProvider<Duration>((ref) => Duration.zero);

/// Which scenario is currently active in the simulator.
final simulatorScenarioProvider =
    StateProvider<Scenario>((ref) => Scenario.approachingPlume);

/// Whether the simulator is auto-advancing time.
final simulatorPlayingProvider = StateProvider<bool>((ref) => false);

/// Fixed start time for every simulator scenario. The scenario advances by
/// moving [simulatorNowProvider] (the offset), NOT by moving this anchor —
/// moving the anchor would just relabel the same curve.
final simulatorAnchorProvider = Provider<DateTime>((ref) {
  return DateTime.now();
});

/// The simulated "now" — fixed anchor + the time offset.
final simulatorNowProvider = Provider<DateTime>((ref) {
  return ref.watch(simulatorAnchorProvider).add(
        ref.watch(simulatorTimeOffsetProvider),
      );
});

/// A [DummyPollutionDataProvider] read at the simulator's clock, so stepping
/// time advances the scenario (current reading moves along the timeline, the
/// forecast window shifts forward) rather than returning the same snapshot.
///
/// Only available in debug mode — production builds use the standard
/// `pollutionDataProvider` binding.
final simulatorDataProvider = Provider<DummyPollutionDataProvider>((ref) {
  final scenario = ref.watch(simulatorScenarioProvider);
  final anchor = ref.watch(simulatorAnchorProvider);
  final now = ref.watch(simulatorNowProvider);
  return DummyPollutionDataProvider(scenario: scenario, anchor: anchor, now: now);
});

/// Controller for the scenario simulator.
///
/// Provides step(), jump(), play/pause, and reset. Only functional
/// in debug mode — production builds get a no-op.
final devScenarioSimulatorProvider =
    NotifierProvider<DevScenarioSimulator, void>(DevScenarioSimulator.new);

class DevScenarioSimulator extends Notifier<void> {
  @override
  void build() {}

  /// Advance time by [step].
  void step([Duration step = const Duration(minutes: 15)]) {
    if (!kDebugMode) return;
    ref.read(simulatorTimeOffsetProvider.notifier).state += step;
  }

  /// Jump to a specific time offset.
  void jumpTo(Duration offset) {
    if (!kDebugMode) return;
    ref.read(simulatorTimeOffsetProvider.notifier).state = offset;
  }

  /// Switch to a different scenario.
  void switchScenario(Scenario scenario) {
    if (!kDebugMode) return;
    ref.read(simulatorScenarioProvider.notifier).state = scenario;
    // Reset time when switching scenarios.
    ref.read(simulatorTimeOffsetProvider.notifier).state = Duration.zero;
    ref.read(alertDedupResetProvider)();
  }

  /// Toggle auto-play.
  void togglePlay() {
    if (!kDebugMode) return;
    final playing = ref.read(simulatorPlayingProvider);
    ref.read(simulatorPlayingProvider.notifier).state = !playing;
  }

  /// Reset to the start of the current scenario.
  void reset() {
    if (!kDebugMode) return;
    ref.read(simulatorTimeOffsetProvider.notifier).state = Duration.zero;
    ref.read(alertDedupResetProvider)();
  }

  /// The demo sequence: cycles through the key moments of the
  /// approachingPlume scenario to show the full alert lifecycle.
  static const demoSteps = [
    Duration(minutes: 0), // Clean — acceptable AQI
    Duration(minutes: 30), // Pollution rising nearby
    Duration(minutes: 45), // Forecast predicts arrival
    Duration(minutes: 60), // Sensitive user notified early
    Duration(minutes: 75), // Local AQI rises
    Duration(minutes: 90), // Escalation if needed
    Duration(minutes: 120), // AQI peaks
    Duration(minutes: 180), // Starts improving
    Duration(minutes: 360), // Recovery
  ];
}

/// Reset the alert dedup state — used when switching scenarios.
final alertDedupResetProvider = Provider<void Function()>((ref) {
  return () {
    // Import cycle avoided: this is a forward reference to the provider
    // defined in alert_providers.dart. The actual reset happens via
    // the provider override in dev_providers.dart.
  };
});
