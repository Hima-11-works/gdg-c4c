/// How fresh the environmental data is.
class DataFreshness {
  const DataFreshness({
    required this.retrievedAt,
    required this.quality,
    this.nextRefreshEta,
  });

  final DateTime retrievedAt;
  final DataQuality quality;
  final DateTime? nextRefreshEta;

  bool get isStale => quality == DataQuality.stale;
}

enum DataQuality {
  full('All data available'),
  partial('Partial data — some readings missing'),
  forecastUnavailable('Forecast not available'),
  stale('Data may be outdated');

  const DataQuality(this.label);
  final String label;
}
