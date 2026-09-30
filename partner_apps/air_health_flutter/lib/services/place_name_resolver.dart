import 'dart:convert';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../domain/models/location_point.dart';

/// A single named geographic place.
class GeoPlace {
  const GeoPlace({
    required this.name,
    required this.latitude,
    required this.longitude,
    this.state = '',
    this.kind = 'city',
  });

  final String name;
  final double latitude;
  final double longitude;
  final String state;
  final String kind;
}

/// Offline reverse geocoding and location name resolver for India.
///
/// Resolves latitude/longitude coordinates to actual human-readable place
/// names (cities, towns, and localities) rather than raw H3 cell IDs.
class PlaceNameResolver {
  PlaceNameResolver._();

  static final PlaceNameResolver instance = PlaceNameResolver._();

  List<GeoPlace>? _places;
  bool _isLoading = false;

  /// Built-in prominent reference places across India to guarantee instant,
  /// zero-latency place resolution even before or without asset loading.
  static const List<GeoPlace> _seedPlaces = [
    // Odisha
    GeoPlace(name: 'Bhubaneswar', latitude: 20.2961, longitude: 85.8245, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Cuttack', latitude: 20.4625, longitude: 85.8830, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Barang', latitude: 20.4071, longitude: 85.8350, state: 'Odisha', kind: 'locality'),
    GeoPlace(name: 'Balianta', latitude: 20.2797, longitude: 85.9023, state: 'Odisha', kind: 'locality'),
    GeoPlace(name: 'Jatani', latitude: 20.1597, longitude: 85.7074, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Khordha', latitude: 20.1827, longitude: 85.6163, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Choudwar', latitude: 20.5392, longitude: 85.9151, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Puri', latitude: 19.8135, longitude: 85.8312, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Rourkela', latitude: 22.2604, longitude: 84.8536, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Berhampur', latitude: 19.3150, longitude: 84.7941, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Sambalpur', latitude: 21.4669, longitude: 83.9812, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Balasore', latitude: 21.4934, longitude: 86.9135, state: 'Odisha', kind: 'city'),
    GeoPlace(name: 'Paradeep', latitude: 20.3164, longitude: 86.6114, state: 'Odisha', kind: 'city'),

    // Delhi NCR
    GeoPlace(name: 'Connaught Place', latitude: 28.6315, longitude: 77.2167, state: 'Delhi', kind: 'locality'),
    GeoPlace(name: 'New Delhi', latitude: 28.6139, longitude: 77.2090, state: 'Delhi', kind: 'city'),
    GeoPlace(name: 'Noida', latitude: 28.5355, longitude: 77.3910, state: 'Uttar Pradesh', kind: 'city'),
    GeoPlace(name: 'Greater Noida', latitude: 28.4744, longitude: 77.5040, state: 'Uttar Pradesh', kind: 'city'),
    GeoPlace(name: 'Gurugram', latitude: 28.4595, longitude: 77.0266, state: 'Haryana', kind: 'city'),
    GeoPlace(name: 'Faridabad', latitude: 28.4089, longitude: 77.3178, state: 'Haryana', kind: 'city'),
    GeoPlace(name: 'Ghaziabad', latitude: 28.6692, longitude: 77.4538, state: 'Uttar Pradesh', kind: 'city'),

    // Metro & Major Cities
    GeoPlace(name: 'Mumbai', latitude: 19.0760, longitude: 72.8777, state: 'Maharashtra', kind: 'city'),
    GeoPlace(name: 'Navi Mumbai', latitude: 19.0330, longitude: 73.0297, state: 'Maharashtra', kind: 'city'),
    GeoPlace(name: 'Thane', latitude: 19.2183, longitude: 72.9781, state: 'Maharashtra', kind: 'city'),
    GeoPlace(name: 'Pune', latitude: 18.5204, longitude: 73.8567, state: 'Maharashtra', kind: 'city'),
    GeoPlace(name: 'Bengaluru', latitude: 12.9716, longitude: 77.5946, state: 'Karnataka', kind: 'city'),
    GeoPlace(name: 'Hyderabad', latitude: 17.3850, longitude: 78.4867, state: 'Telangana', kind: 'city'),
    GeoPlace(name: 'Chennai', latitude: 13.0827, longitude: 80.2707, state: 'Tamil Nadu', kind: 'city'),
    GeoPlace(name: 'Kolkata', latitude: 22.5726, longitude: 88.3639, state: 'West Bengal', kind: 'city'),
    GeoPlace(name: 'Ahmedabad', latitude: 23.0225, longitude: 72.5714, state: 'Gujarat', kind: 'city'),
    GeoPlace(name: 'Surat', latitude: 21.1702, longitude: 72.8311, state: 'Gujarat', kind: 'city'),
    GeoPlace(name: 'Jaipur', latitude: 26.9124, longitude: 75.7873, state: 'Rajasthan', kind: 'city'),
    GeoPlace(name: 'Lucknow', latitude: 26.8467, longitude: 80.9462, state: 'Uttar Pradesh', kind: 'city'),
    GeoPlace(name: 'Kanpur', latitude: 26.4499, longitude: 80.3319, state: 'Uttar Pradesh', kind: 'city'),
    GeoPlace(name: 'Patna', latitude: 25.5941, longitude: 85.1376, state: 'Bihar', kind: 'city'),
    GeoPlace(name: 'Ranchi', latitude: 23.3441, longitude: 85.3096, state: 'Jharkhand', kind: 'city'),
    GeoPlace(name: 'Bhopal', latitude: 23.2599, longitude: 77.4126, state: 'Madhya Pradesh', kind: 'city'),
    GeoPlace(name: 'Indore', latitude: 22.7196, longitude: 75.8577, state: 'Madhya Pradesh', kind: 'city'),
    GeoPlace(name: 'Chandigarh', latitude: 30.7333, longitude: 76.7794, state: 'Chandigarh', kind: 'city'),
    GeoPlace(name: 'Dehradun', latitude: 30.3165, longitude: 78.0322, state: 'Uttarakhand', kind: 'city'),
    GeoPlace(name: 'Shimla', latitude: 31.1048, longitude: 77.1734, state: 'Himachal Pradesh', kind: 'city'),
    GeoPlace(name: 'Srinagar', latitude: 34.0837, longitude: 74.7973, state: 'Jammu and Kashmir', kind: 'city'),
    GeoPlace(name: 'Guwahati', latitude: 26.1445, longitude: 91.7362, state: 'Assam', kind: 'city'),
    GeoPlace(name: 'Kochi', latitude: 9.9312, longitude: 76.2673, state: 'Kerala', kind: 'city'),
    GeoPlace(name: 'Thiruvananthapuram', latitude: 8.5241, longitude: 76.9366, state: 'Kerala', kind: 'city'),
    GeoPlace(name: 'Coimbatore', latitude: 11.0168, longitude: 76.9558, state: 'Tamil Nadu', kind: 'city'),
    GeoPlace(name: 'Visakhapatnam', latitude: 17.6868, longitude: 83.2185, state: 'Andhra Pradesh', kind: 'city'),
    GeoPlace(name: 'Vijayawada', latitude: 16.5062, longitude: 80.6480, state: 'Andhra Pradesh', kind: 'city'),
    GeoPlace(name: 'Nagpur', latitude: 21.1458, longitude: 79.0882, state: 'Maharashtra', kind: 'city'),
  ];

  /// Asynchronously loads the full India places dataset (10,000+ places).
  Future<void> ensureLoaded() async {
    if (_places != null || _isLoading) return;
    _isLoading = true;
    try {
      final jsonStr = await rootBundle.loadString('assets/data/india_locations.json');
      final list = jsonDecode(jsonStr) as List<dynamic>;
      final parsed = <GeoPlace>[];
      for (final item in list) {
        if (item is Map<String, dynamic>) {
          final n = item['n'] as String?;
          final lat = (item['lat'] as num?)?.toDouble();
          final lon = (item['lon'] as num?)?.toDouble();
          if (n != null && lat != null && lon != null) {
            parsed.add(GeoPlace(
              name: n,
              latitude: lat,
              longitude: lon,
              state: (item['s'] as String?) ?? '',
              kind: (item['t'] as String?) ?? 'locality',
            ));
          }
        }
      }
      if (parsed.isNotEmpty) {
        _places = parsed;
      }
    } catch (e) {
      debugPrint('[PlaceNameResolver] Asset loading note: $e (using seed places)');
    } finally {
      _isLoading = false;
      _places ??= _seedPlaces;
    }
  }

  /// Calculates the Haversine distance in kilometers between two points.
  static double distanceKm(double lat1, double lon1, double lat2, double lon2) {
    const r = 6371.0;
    final dLat = (lat2 - lat1) * math.pi / 180.0;
    final dLon = (lon2 - lon1) * math.pi / 180.0;
    final a = math.sin(dLat / 2) * math.sin(dLat / 2) +
        math.cos(lat1 * math.pi / 180.0) *
            math.cos(lat2 * math.pi / 180.0) *
            math.sin(dLon / 2) *
            math.sin(dLon / 2);
    final c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a));
    return r * c;
  }

  /// Calculates the compass cardinal direction from (lat1, lon1) towards (lat2, lon2).
  static String cardinalDirection(double lat1, double lon1, double lat2, double lon2) {
    final dLon = (lon2 - lon1) * math.pi / 180.0;
    final y = math.sin(dLon) * math.cos(lat2 * math.pi / 180.0);
    final x = math.cos(lat1 * math.pi / 180.0) * math.sin(lat2 * math.pi / 180.0) -
        math.sin(lat1 * math.pi / 180.0) * math.cos(lat2 * math.pi / 180.0) * math.cos(dLon);
    var bearing = math.atan2(y, x) * 180.0 / math.pi;
    bearing = (bearing + 360.0) % 360.0;
    const directions = ['North', 'Northeast', 'East', 'Southeast', 'South', 'Southwest', 'West', 'Northwest'];
    final index = ((bearing + 22.5) % 360 / 45).floor();
    return directions[index % 8];
  }

  /// Synchronously resolves coordinates into a human-readable location name.
  ///
  /// Priority:
  /// 1. Nearest locality or city within 15 km (e.g. "Barang", "Cuttack", "Noida").
  /// 2. If reference location is available (e.g. user location), gives a natural
  ///    directional title such as "Northeast Area (8 km)" or "North of Bhubaneswar".
  /// 3. Falls back to nearest regional city title rather than any H3 hex code.
  String resolve({
    required double latitude,
    required double longitude,
    LocationPoint? referenceLocation,
  }) {
    final pool = _places ?? _seedPlaces;

    GeoPlace? bestMatch;
    double bestDistance = double.infinity;

    for (final place in pool) {
      final dist = distanceKm(latitude, longitude, place.latitude, place.longitude);
      if (dist < bestDistance) {
        bestDistance = dist;
        bestMatch = place;
        if (dist < 1.0) break; // Direct match
      }
    }

    if (bestMatch != null && bestDistance <= 12.0) {
      if (bestDistance <= 3.0) {
        return bestMatch.name;
      }
      return '${bestMatch.name} Area';
    }

    // If beyond immediate landmark radius, use direction relative to reference location
    if (referenceLocation != null) {
      final distFromRef = distanceKm(
        referenceLocation.latitude,
        referenceLocation.longitude,
        latitude,
        longitude,
      );
      final direction = cardinalDirection(
        referenceLocation.latitude,
        referenceLocation.longitude,
        latitude,
        longitude,
      );
      final refLabel = referenceLocation.label;
      if (refLabel != null && refLabel.isNotEmpty && !refLabel.startsWith('88')) {
        return '$direction of $refLabel';
      }
      return '$direction Area (${distFromRef.round()} km)';
    }

    if (bestMatch != null) {
      return '${bestMatch.name} Region';
    }

    return 'Nearby Region';
  }
}
