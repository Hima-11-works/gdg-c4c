import '../../domain/models/models.dart';
import '../pollution_data_provider.dart';
import 'scenario_data.dart';

/// Deterministic dummy data provider.
///
/// Select a [Scenario] to get reproducible environmental data for that
/// situation. All times are relative to [anchor] (defaults to
/// `DateTime.now()`; pass a fixed value in tests).
///
/// Widgets and feature screens never instantiate this directly — they
/// read it through the `pollutionDataProvider` Riverpod provider.
class DummyPollutionDataProvider implements PollutionDataProvider {
  DummyPollutionDataProvider({
    this.scenario = Scenario.cleanStable,
    DateTime? anchor,
  }) : anchor = anchor ?? DateTime.now();

  final Scenario scenario;
  final DateTime anchor;

  ScenarioData get _data => buildScenario(scenario, anchor);

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
      return f.at.difference(anchor).inHours <= maxHours;
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
