import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/models/location_point.dart';
import '../services/location_service.dart';

/// LocationService instance.
final locationServiceProvider = Provider<LocationService>((ref) {
  return const LocationService();
});

/// Current permission status — async, can be re-checked.
final locationPermissionProvider =
    FutureProvider<LocationPermissionStatus>((ref) async {
  final service = ref.read(locationServiceProvider);
  return service.checkPermission();
});

/// The user's current location.
///
/// Tries to get the real device location; falls back to
/// [LocationService.fallbackLocation] (Bhubaneswar) if permission
/// is denied or unavailable. The app is always usable in demo mode
/// without location permission.
final currentLocationProvider = FutureProvider<LocationPoint>((ref) async {
  final service = ref.read(locationServiceProvider);
  final result = await service.requestAndLocate();

  return switch (result) {
    LocationSuccess(:final location) => location,
    _ => LocationService.fallbackLocation,
  };
});

/// Synchronous wrapper — resolves once on first read, then caches.
/// Screens that just need the label use this; providers that need
/// to await the real position use [currentLocationProvider].
final resolvedLocationProvider = Provider<LocationPoint>((ref) {
  final asyncLoc = ref.watch(currentLocationProvider);
  return asyncLoc.valueOrNull ?? LocationService.fallbackLocation;
});
