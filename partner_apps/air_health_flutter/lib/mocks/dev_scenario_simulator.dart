import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/providers/dummy_pollution_data_provider.dart';
import '../data/providers/scenario_data.dart';

/// Time offset for the scenario simulator — how far "now" has advanced
/// from the initial anchor.
final simulatorTimeOffsetProvider =
    StateProvider<Duration>((ref) => Duration.zero);

/// Which scenario is currently active in the simulator.
final simulatorScenarioProvider =
    StateProvider<Scenario>((ref) => Scenario.approachingPlume);

/// Whether the simulator is auto-advancing time.
final simulatorPlayingProvider = StateProvider<bool>((ref) => false);

/// The simulated "now" — anchor + offset.
final simulatorNowProvider = Provider<DateTime>((ref) {
  return DateTime(2026, 9, 17, 10, 0).add(
    ref.watch(simulatorTimeOffsetProvider),
  );
});

/// A [DummyPollutionDataProvider] that uses the simulator's time.
///
/// Only available in debug mode — production builds use the standard
/// `pollutionDataProvider` binding.
final simulatorDataProvider = Provider<DummyPollutionDataProvider>((ref) {
  final scenario = ref.watch(simulatorScenarioProvider);
  final now = ref.watch(simulatorNowProvider);
  return DummyPollutionDataProvider(scenario: scenario, anchor: now);
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

/// Auto-advance timer — steps the simulator every 750ms when playing.
final simulatorAutoAdvanceProvider = Provider<void>((ref) {
  if (!kDebugMode) return;

  ref.listen<bool>(simulatorPlayingProvider, (prev, playing) {
    if (playing) {
      Future.doWhile(() async {
        await Future.delayed(const Duration(milliseconds: 750));
        if (!ref.exists(simulatorPlayingProvider)) return false;
        if (!ref.read(simulatorPlayingProvider)) return false;
        ref.read(simulatorTimeOffsetProvider.notifier).state +=
            const Duration(minutes: 15);
        return true;
      });
    }
  });
});
