import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/pollution_data_provider.dart';
import '../data/providers/dummy_pollution_data_provider.dart';

/// The single binding point for [PollutionDataProvider].
///
/// To switch to a real API later, change ONE override here —
/// no screen, widget, alert engine, or chart needs to know.
final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
  // Default: dummy provider. Override in main() or tests to swap.
  return DummyPollutionDataProvider();
});
