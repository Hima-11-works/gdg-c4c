import '../../domain/models/models.dart';
import '../../domain/pm25_aqi.dart';
import '../grid/grid_api.dart';
import '../pollution_data_provider.dart';

/// Thrown when the grid API cannot resolve usable data for a location
/// (e.g. no cell near it has a PM2.5 estimate yet). Callers already handle
/// provider errors through the usual Riverpod `AsyncValue` error states.
class PollutionDataUnavailable implements Exception {
  const PollutionDataUnavailable(this.message);

  final String message;

  @override
  String toString() => 'PollutionDataUnavailable: $message';
}

/// [PollutionDataProvider] backed by the platform's grid API.
///
/// This is the app-side half of the integration: it speaks the published
/// `/api/v2/...` contract (H3 cells, PM2.5/PDI, run id and provenance) and maps
/// it onto the app's domain models, so nothing above the data layer changes.
///
/// Mapping notes — the backend and the app describe different things:
///  - The app is single-location; the API is a grid. It picks the nearest
///    published cell using coordinates attached to `/grid/current`, falling
///    back to `/weather` only when a snapshot omits them.
///  - The app's AQI is derived from the cell's PM2.5 via [pm25ToCpcbAqi].
///  - `/grid/forecast` returns one supported horizon per request, so a
///    forecast series is assembled from the exact horizons in `/meta`.
///  - "Nearby areas" are the nearest cells in a slightly larger box.
///  - "Pollution events" are `/alerts` for the user's cell.
class GridApiPollutionDataProvider implements PollutionDataProvider {
  GridApiPollutionDataProvider({
    required GridApiClient client,
    this.cellRadiusDeg = 0.06,
    this.nearbyRadiusDeg = 0.12,
    this.maxNearbyAreas = 6,
    this.nearbyForecastMinutes = 180,
  }) : _client = client;

  final GridApiClient _client;

  /// Half-size of the box used to locate the user's cell (degrees).
  final double cellRadiusDeg;

  /// Half-size of the box used to enumerate nearby cells (degrees).
  final double nearbyRadiusDeg;

  /// Most nearby areas to return.
  final int maxNearbyAreas;

  /// Horizon used for each nearby area's single forecast point.
  final int nearbyForecastMinutes;

  DateTime? _lastGeneratedAt;
  bool _lastIsDemo = false;
  String? _lastMode;
  String? _lastRunId;

  /// Whether the most recent response was backend demo data. Surfaced for
  /// callers that want to reflect it.
  bool get isDemo => _lastIsDemo;

  // ── PollutionDataProvider ────────────────────────────────────────────

  @override
  Future<AirQualityReading> getCurrentAirQuality(
    LocationPoint location,
  ) async {
    final snapshot = await _nearestCell(location);
    return snapshot.reading;
  }

  @override
  Future<List<ForecastPoint>> getForecast(
    LocationPoint location,
    Duration horizon,
  ) async {
    final publication = await _client.latestPublication();
    final snapshot = await _nearestCell(location, publication: publication);
    final supported = publication.supportedForecastMinutes;
    final steps = _forecastSteps(horizon, supported);
    if (steps.isEmpty) return const [];

    final bounds = GeoBounds.around(location, cellRadiusDeg);
    final points = <ForecastPoint>[];
    for (final minutes in steps) {
      final envelope = await _client.forecast(
        minutes: minutes,
        bounds: bounds,
        runId: publication.runId,
      );
      _recordEnvelope(envelope);
      for (final f in envelope.data) {
        if (f.h3Cell != snapshot.h3Cell) continue;
        points.add(ForecastPoint(
          at: f.forecastTime,
          aqiCpcb: pm25ToCpcbAqi(f.predictedPm25),
          pm25: f.predictedPm25,
          confidence: f.confidence,
        ));
      }
    }
    points.sort((a, b) => a.at.compareTo(b.at));
    return points;
  }

  @override
  Future<List<NearbyArea>> getNearbyAreas(LocationPoint location) async {
    final publication = await _client.latestPublication();
    final bounds = GeoBounds.around(location, nearbyRadiusDeg);
    final current = await _client.current(
      bounds: bounds,
      runId: publication.runId,
    );
    final fallbackWeather = await _fallbackWeather(
      current.data,
      bounds,
      runId: publication.runId,
    );
    final supported = publication.supportedForecastMinutes;
    final eligibleHorizons = supported
        .where((minutes) => minutes <= nearbyForecastMinutes)
        .toList();
    final forecastMinutes = eligibleHorizons.isNotEmpty
        ? eligibleHorizons.last
        : (supported.isNotEmpty ? supported.first : null);
    final forecast = forecastMinutes == null
        ? null
        : await _client.forecast(
            minutes: forecastMinutes,
            bounds: bounds,
            runId: publication.runId,
          );
    _recordEnvelope(current);
    if (forecast != null) _recordEnvelope(forecast);

    final forecastByCell = <String, ForecastDto>{};
    for (final f in forecast?.data ?? const <ForecastDto>[]) {
      forecastByCell.putIfAbsent(f.h3Cell, () => f);
    }

    final snapshots = <_CellSnapshot>[];
    for (final state in current.data) {
      if (state.pm25 == null) continue;
      final weather = fallbackWeather[state.h3Cell];
      final latitude = state.latitude ?? weather?.latitude;
      final longitude = state.longitude ?? weather?.longitude;
      if (latitude == null || longitude == null) continue;
      final point = LocationPoint(
        latitude: latitude,
        longitude: longitude,
        label: state.h3Cell,
      );
      snapshots.add(_CellSnapshot(
        h3Cell: state.h3Cell,
        runId: publication.runId,
        pm25: state.pm25!,
        confidence: state.confidence,
        location: point,
        distanceKm: location.distanceTo(point),
        retrievedAt: current.generatedAt,
        isDemo: current.isDemo,
      ));
    }
    snapshots.sort((a, b) => a.distanceKm.compareTo(b.distanceKm));

    // Drop the closest cell — that's the user's own area, not "nearby".
    final others =
        snapshots.length > 1 ? snapshots.sublist(1) : <_CellSnapshot>[];
    return others.take(maxNearbyAreas).map((s) {
      final fc = forecastByCell[s.h3Cell];
      return NearbyArea(
        location: s.location,
        name: _cellLabel(s.h3Cell),
        aqiNow: pm25ToCpcbAqi(s.pm25),
        forecast: _areaForecast(s, fc),
        trend: _trend(s.pm25, fc),
        distanceKm: s.distanceKm,
        confidence: s.confidence,
      );
    }).toList();
  }

  @override
  Future<List<PollutionEvent>> getPollutionEvents(
    LocationPoint location,
  ) async {
    final snapshot = await _nearestCell(location);
    final alerts = await _client.alerts(runId: snapshot.runId);
    _recordEnvelope(alerts);

    return alerts.data
        .where((a) => a.h3Cell == snapshot.h3Cell)
        .map((a) {
      final peakPm25 = a.forecastPm25 ?? a.currentPm25 ?? snapshot.pm25;
      return PollutionEvent(
        id: '${a.h3Cell}-${a.severity}-${a.createdAt.millisecondsSinceEpoch}',
        sourceArea: _cellLabel(a.h3Cell),
        expectedArrivalAt: a.forecastTime ?? a.createdAt,
        peakAqiEstimate: pm25ToCpcbAqi(peakPm25),
        confidence: a.confidence ?? 0.5,
        description: a.message,
      );
    }).toList();
  }

  @override
  Future<DataFreshness> getDataFreshness() async {
    if (_lastGeneratedAt == null) {
      // No location argument, and we haven't fetched anything yet — the
      // alerts endpoint is the cheapest way to obtain a `generated_at`.
      final publication = await _client.latestPublication();
      final envelope = await _client.alerts(runId: publication.runId);
      _recordEnvelope(envelope);
    }
    return DataFreshness(
      retrievedAt: _lastGeneratedAt!,
      quality: DataQuality.full,
      isDemo: _lastIsDemo,
      mode: _lastMode,
      runId: _lastRunId,
    );
  }

  // ── Internals ────────────────────────────────────────────────────────

  /// The cell nearest [location] that has a PM2.5 estimate.
  Future<_CellSnapshot> _nearestCell(
    LocationPoint location, {
    GridPublication? publication,
  }) async {
    final pinnedPublication = publication ?? await _client.latestPublication();
    final bounds = GeoBounds.around(location, cellRadiusDeg);
    final current = await _client.current(
      bounds: bounds,
      runId: pinnedPublication.runId,
    );
    final fallbackWeather = await _fallbackWeather(
      current.data,
      bounds,
      runId: pinnedPublication.runId,
    );
    _recordEnvelope(current);

    _CellSnapshot? best;
    for (final state in current.data) {
      if (state.pm25 == null) continue;
      final weather = fallbackWeather[state.h3Cell];
      final latitude = state.latitude ?? weather?.latitude;
      final longitude = state.longitude ?? weather?.longitude;
      if (latitude == null || longitude == null) continue;
      final point = LocationPoint(
        latitude: latitude,
        longitude: longitude,
        label: state.h3Cell,
      );
      final distance = location.distanceTo(point);
      if (best == null || distance < best.distanceKm) {
        best = _CellSnapshot(
          h3Cell: state.h3Cell,
          runId: pinnedPublication.runId,
          pm25: state.pm25!,
          confidence: state.confidence,
          location: point,
          distanceKm: distance,
          retrievedAt: current.generatedAt,
          isDemo: current.isDemo,
        );
      }
    }

    if (best == null) {
      throw const PollutionDataUnavailable(
        'No grid cell with a PM2.5 estimate was found near this location.',
      );
    }
    return best;
  }

  Future<Map<String, WeatherDto>> _fallbackWeather(
    List<GridStateDto> states,
    GeoBounds bounds, {
    required String runId,
  }) async {
    if (!states.any((state) => state.latitude == null || state.longitude == null)) {
      return const <String, WeatherDto>{};
    }
    final envelope = await _client.weather(bounds: bounds, runId: runId);
    return {for (final row in envelope.data) row.h3Cell: row};
  }

  void _recordEnvelope<T>(GridEnvelope<T> envelope) {
    _lastGeneratedAt = envelope.generatedAt;
    _lastIsDemo = envelope.isDemo;
    _lastMode = envelope.mode;
    _lastRunId = envelope.runId;
  }

  /// Select only horizons actually published by the pinned run.
  static List<int> _forecastSteps(Duration horizon, List<int> supported) {
    final capped = horizon.inMinutes > 360 ? 360 : horizon.inMinutes;
    if (capped <= 0) return const [];
    final selected =
        supported.where((minutes) => minutes > 0 && minutes <= capped).toSet().toList();
    selected.sort();
    return selected;
  }

  static List<ForecastPoint> _areaForecast(
    _CellSnapshot snap,
    ForecastDto? fc,
  ) {
    final points = <ForecastPoint>[
      ForecastPoint(
        at: snap.retrievedAt,
        aqiCpcb: pm25ToCpcbAqi(snap.pm25),
        pm25: snap.pm25,
        confidence: snap.confidence,
      ),
    ];
    if (fc != null) {
      points.add(ForecastPoint(
        at: fc.forecastTime,
        aqiCpcb: pm25ToCpcbAqi(fc.predictedPm25),
        pm25: fc.predictedPm25,
        confidence: fc.confidence,
      ));
    }
    return points;
  }

  static AreaTrend _trend(double currentPm25, ForecastDto? fc) {
    if (fc == null) return AreaTrend.stable;
    if (fc.predictedPm25 > currentPm25 * 1.05) return AreaTrend.worsening;
    if (fc.predictedPm25 < currentPm25 * 0.95) return AreaTrend.improving;
    return AreaTrend.stable;
  }

  static String _cellLabel(String h3Cell) =>
      h3Cell.length <= 7 ? h3Cell : '${h3Cell.substring(0, 7)}…';
}

/// An H3 cell with the fields the adapter needs to build domain models.
class _CellSnapshot {
  const _CellSnapshot({
    required this.h3Cell,
    required this.runId,
    required this.pm25,
    required this.confidence,
    required this.location,
    required this.distanceKm,
    required this.retrievedAt,
    required this.isDemo,
  });

  final String h3Cell;
  final String runId;
  final double pm25;
  final double confidence;
  final LocationPoint location;
  final double distanceKm;
  final DateTime retrievedAt;
  final bool isDemo;

  AirQualityReading get reading => AirQualityReading(
        aqiCpcb: pm25ToCpcbAqi(pm25),
        pm25: pm25,
        primaryPollutant: 'PM2.5',
        category: pm25ToCategory(pm25),
        recordedAt: retrievedAt,
      );
}
