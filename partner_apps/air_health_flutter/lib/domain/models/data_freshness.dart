/// How fresh the environmental data is.
class DataFreshness {
  const DataFreshness({
    required this.retrievedAt,
    required this.quality,
    this.nextRefreshEta,
    this.isDemo = false,
    this.mode,
    this.runId,
  });

  final DateTime retrievedAt;
  final DataQuality quality;
  final DateTime? nextRefreshEta;
  final bool isDemo;
  final String? mode;
  final String? runId;

  bool get isStale => quality == DataQuality.stale;

  /// How old the data is right now.
  Duration get age => DateTime.now().difference(retrievedAt);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is DataFreshness &&
          retrievedAt == other.retrievedAt &&
          quality == other.quality &&
          nextRefreshEta == other.nextRefreshEta &&
          isDemo == other.isDemo &&
          mode == other.mode &&
          runId == other.runId;

  @override
  int get hashCode => Object.hash(retrievedAt, quality, nextRefreshEta, isDemo, mode, runId);

  @override
  String toString() =>
      'DataFreshness(quality=${quality.name}, mode=$mode, age=$age)';
}

enum DataQuality {
  full('All data available'),
  partial('Partial data — some readings missing'),
  forecastUnavailable('Forecast not available'),
  stale('Data may be outdated');

  const DataQuality(this.label);
  final String label;
}
