import 'package:geolocator/geolocator.dart';

import '../domain/models/location_point.dart';

/// Result of a location request.
sealed class LocationResult {
  const LocationResult();
}

/// Location obtained successfully.
class LocationSuccess extends LocationResult {
  const LocationSuccess(this.location);
  final LocationPoint location;
}

/// Permission was denied — user can be prompted to grant it.
class LocationDenied extends LocationResult {
  const LocationDenied();
}

/// Permission was permanently denied — user must go to system settings.
class LocationPermanentlyDenied extends LocationResult {
  const LocationPermanentlyDenied();
}

/// Location services are disabled on the device.
class LocationUnavailable extends LocationResult {
  const LocationUnavailable();
}

/// Wraps geolocator — the only file that imports the plugin.
///
/// Handles permission flow, fallback to demo location, and error
/// states. Widgets never call geolocator directly.
class LocationService {
  const LocationService();

  /// Fallback location used when permission is denied or unavailable.
  /// Bhubaneswar — matches the dummy data provider's default.
  static const fallbackLocation = LocationPoint(
    latitude: 20.2961,
    longitude: 85.8245,
    label: 'Bhubaneswar',
    isFallback: true,
  );

  /// Request location permission and return the current position.
  ///
  /// Returns [LocationSuccess] with the device location,
  /// [LocationDenied] / [LocationPermanentlyDenied] if the user
  /// refuses, or [LocationUnavailable] if location services are off.
  Future<LocationResult> requestAndLocate() async {
    // Check if location services are enabled.
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      return const LocationUnavailable();
    }

    // Check current permission state.
    var permission = await Geolocator.checkPermission();

    // Request permission if not yet determined.
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
      if (permission == LocationPermission.denied) {
        return const LocationDenied();
      }
    }

    // Permanently denied — user must go to settings.
    if (permission == LocationPermission.deniedForever) {
      return const LocationPermanentlyDenied();
    }

    return _readCurrentPosition();
  }

  /// Get a fresh device location only when permission is already granted.
  /// Periodic/resume refreshes use this so they never reopen an OS prompt.
  Future<LocationResult> locateIfPermitted() async {
    final permission = await checkPermission();
    if (permission == LocationPermissionStatus.serviceDisabled) {
      return const LocationUnavailable();
    }
    if (permission == LocationPermissionStatus.permanentlyDenied) {
      return const LocationPermanentlyDenied();
    }
    if (permission == LocationPermissionStatus.denied) {
      return const LocationDenied();
    }
    return _readCurrentPosition();
  }

  Future<LocationResult> _readCurrentPosition() async {
    try {
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.medium,
        ),
      );
      return LocationSuccess(
        LocationPoint(
          latitude: position.latitude,
          longitude: position.longitude,
        ),
      );
    } catch (_) {
      // Timeout or other geolocator error.
      return const LocationUnavailable();
    }
  }

  /// Get the current permission status without requesting.
  Future<LocationPermissionStatus> checkPermission() async {
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) return LocationPermissionStatus.serviceDisabled;

    final permission = await Geolocator.checkPermission();
    return switch (permission) {
      LocationPermission.denied => LocationPermissionStatus.denied,
      LocationPermission.deniedForever =>
        LocationPermissionStatus.permanentlyDenied,
      LocationPermission.whileInUse ||
      LocationPermission.always =>
        LocationPermissionStatus.granted,
      _ => LocationPermissionStatus.denied,
    };
  }

  /// Open the app's location settings (for permanently-denied cases).
  Future<void> openAppSettings() async {
    await Geolocator.openAppSettings();
  }

  /// Open the device's location services settings.
  Future<void> openLocationSettings() async {
    await Geolocator.openLocationSettings();
  }
}

enum LocationPermissionStatus {
  granted,
  denied,
  permanentlyDenied,
  serviceDisabled,
}
