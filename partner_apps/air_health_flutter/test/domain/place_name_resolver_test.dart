import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';
import 'package:air_health_flutter/services/place_name_resolver.dart';

void main() {
  group('PlaceNameResolver', () {
    final resolver = PlaceNameResolver.instance;

    test('resolves known cities accurately', () {
      final name = resolver.resolve(
        latitude: 20.4625,
        longitude: 85.8830,
      );
      expect(name, 'Cuttack');
    });

    test('resolves nearby localities with area descriptor when close', () {
      final name = resolver.resolve(
        latitude: 20.41,
        longitude: 85.84,
      );
      expect(name.toLowerCase(), contains('barang'));
    });

    test('falls back to direction relative to user location when outside landmark radius', () {
      const userLoc = LocationPoint(
        latitude: 20.2961,
        longitude: 85.8245,
        label: 'Bhubaneswar',
      );
      final name = resolver.resolve(
        latitude: 20.90,
        longitude: 85.8245,
        referenceLocation: userLoc,
      );
      expect(name, contains('North'));
      expect(name, isNot(contains('88')));
    });

    test('never returns an H3 hexadecimal cell ID', () {
      final name = resolver.resolve(
        latitude: 28.5355,
        longitude: 77.3910,
      );
      expect(name, isNot(matches(r'^[0-9a-f]{15}$')));
      expect(name, 'Noida');
    });
  });
}
