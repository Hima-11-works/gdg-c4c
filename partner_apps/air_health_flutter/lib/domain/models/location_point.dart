/// A geographic point the app cares about.
///
/// [label] is an optional human-readable name (e.g. "Bhubaneswar").
class LocationPoint {
  const LocationPoint({
    required this.latitude,
    required this.longitude,
    this.label,
  });

  final double latitude;
  final double longitude;
  final String? label;

  @override
  String toString() =>
      'LocationPoint($latitude, $longitude${label != null ? ", $label" : ""})';
}
