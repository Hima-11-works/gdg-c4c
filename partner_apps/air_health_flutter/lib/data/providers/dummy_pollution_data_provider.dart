import '../../domain/models/models.dart';
import '../pollution_data_provider.dart';
import 'scenario_data.dart';

/// Deterministic dummy data provider.
///
/// Select a [Scenario] to get reproducible environmental data for that
/// situation. [anchor] is the scenario's fixed start; [now] is the clock the
/// data is read at. When [now] is later than [anchor] the scenario advances
/// along its timeline (see [buildScenarioSnapshot]) — that is what lets the
/// dev simulator move time and watch the situation evolve. Both default to
/// `DateTime.now()` (so `now == anchor`, i.e. the authored snapshot); pass
/// fixed values in tests.
///
/// Widgets and feature screens never instantiate this directly — they
/// read it through the `pollutionDataProvider` Riverpod provider.
class DummyPollutionDataProvider implements PollutionDataProvider {
  DummyPollutionDataProvider({
    this.scenario = Scenario.cleanStable,
    DateTime? anchor,
    DateTime? now,
  })  : anchor = anchor ?? now ?? DateTime.now(),
        now = now ?? anchor ?? DateTime.now();

  final Scenario scenario;

  /// Fixed start of the scenario.
  final DateTime anchor;

  /// The clock this provider is read at; advancing it advances the scenario.
  final DateTime now;

  late final ScenarioData _data =
      buildScenarioSnapshot(scenario, anchor, now);

  @override
  Future<AirQualityReading> getCurrentAirQuality(LocationPoint location) async {
    return _data.reading;
  }

  @override
  Future<List<ForecastPoint>> getForecast(
    LocationPoint location,
    Duration horizon,
  ) async {
    final maxHours = horizon.inHours;
    return _data.forecast.where((f) {
      return f.at.difference(now).inHours <= maxHours;
    }).toList();
  }

  @override
  Future<List<NearbyArea>> getNearbyAreas(LocationPoint location) async {
    return _data.nearbyAreas;
  }

  @override
  Future<List<PollutionEvent>> getPollutionEvents(LocationPoint location) async {
    return _data.events;
  }

  @override
  Future<DataFreshness> getDataFreshness() async {
    return _data.freshness;
  }
}
