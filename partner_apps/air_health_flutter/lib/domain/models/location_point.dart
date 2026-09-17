import 'dart:math' as math;

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

  /// Distance in kilometres to another point (Haversine).
  double distanceTo(LocationPoint other) {
    const earthRadiusKm = 6371.0;
    final dLat = _degToRad(other.latitude - latitude);
    final dLon = _degToRad(other.longitude - longitude);
    final a = math.sin(dLat / 2) * math.sin(dLat / 2) +
        math.cos(_degToRad(latitude)) *
            math.cos(_degToRad(other.latitude)) *
            math.sin(dLon / 2) *
            math.sin(dLon / 2);
    return earthRadiusKm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a));
  }

  static double _degToRad(double deg) => deg * math.pi / 180;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is LocationPoint &&
          latitude == other.latitude &&
          longitude == other.longitude &&
          label == other.label;

  @override
  int get hashCode => Object.hash(latitude, longitude, label);

  @override
  String toString() =>
      'LocationPoint($latitude, $longitude${label != null ? ", $label" : ""})';
}
