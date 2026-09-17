import '../domain/models/models.dart';

/// Abstract interface for environmental pollution data.
///
/// The app depends ONLY on this interface — never on a concrete
/// implementation. Swap [DummyPollutionDataProvider] for
/// [RemotePollutionDataProvider] later by changing a single Riverpod
/// override; no screen, widget, alert engine, or chart needs to change.
abstract class PollutionDataProvider {
  /// Current AQI reading for [location].
  Future<AirQualityReading> getCurrentAirQuality(LocationPoint location);

  /// Hourly forecast for [location] up to [horizon] from now.
  Future<List<ForecastPoint>> getForecast(
    LocationPoint location,
    Duration horizon,
  );

  /// Nearby areas with their own AQI snapshots.
  Future<List<NearbyArea>> getNearbyAreas(LocationPoint location);

  /// Pollution events approaching [location].
  Future<List<PollutionEvent>> getPollutionEvents(LocationPoint location);

  /// How fresh the data currently is.
  Future<DataFreshness> getDataFreshness();
}
