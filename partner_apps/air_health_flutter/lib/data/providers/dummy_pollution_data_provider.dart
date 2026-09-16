import '../../domain/models/models.dart';
import '../pollution_data_provider.dart';

/// Deterministic dummy data provider — the only implementation used
/// until a real API is available. Returns hardcoded "moderate" data
/// so the app compiles and renders. Scenarios will be added in H5.
class DummyPollutionDataProvider implements PollutionDataProvider {
  @override
  Future<AirQualityReading> getCurrentAirQuality(LocationPoint location) async {
    return AirQualityReading(
      aqiCpcb: 142,
      pm25: 82.5,
      primaryPollutant: 'PM2.5',
      category: CpcbCategory.moderate,
      recordedAt: DateTime.now(),
    );
  }

  @override
  Future<List<ForecastPoint>> getForecast(
    LocationPoint location,
    Duration horizon,
  ) async {
    final now = DateTime.now();
    return List.generate(12, (i) {
      return ForecastPoint(
        at: now.add(Duration(hours: i + 1)),
        aqiCpcb: 140 + (i * 5),
        pm25: 80.0 + (i * 3),
        confidence: 0.85,
      );
    });
  }

  @override
  Future<List<NearbyArea>> getNearbyAreas(LocationPoint location) async {
    return const [];
  }

  @override
  Future<List<PollutionEvent>> getPollutionEvents(LocationPoint location) async {
    return const [];
  }

  @override
  Future<DataFreshness> getDataFreshness() async {
    return DataFreshness(
      retrievedAt: DateTime.now(),
      quality: DataQuality.full,
    );
  }
}
