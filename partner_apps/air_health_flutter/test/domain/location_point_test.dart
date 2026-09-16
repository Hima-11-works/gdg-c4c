import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';

void main() {
  group('LocationPoint', () {
    const bhubaneswar = LocationPoint(
      latitude: 20.2961,
      longitude: 85.8245,
      label: 'Bhubaneswar',
    );
    const same = LocationPoint(
      latitude: 20.2961,
      longitude: 85.8245,
      label: 'Bhubaneswar',
    );
    const different = LocationPoint(
      latitude: 28.6139,
      longitude: 77.209,
      label: 'Delhi',
    );
    const noLabel = LocationPoint(latitude: 20.2961, longitude: 85.8245);

    test('equality — same values are equal', () {
      expect(bhubaneswar, equals(same));
      expect(bhubaneswar.hashCode, equals(same.hashCode));
    });

    test('equality — different values are not equal', () {
      expect(bhubaneswar, isNot(equals(different)));
    });

    test('equality — label matters', () {
      expect(bhubaneswar, isNot(equals(noLabel)));
    });

    test('toString includes label when present', () {
      expect(bhubaneswar.toString(), contains('Bhubaneswar'));
    });

    test('toString omits label when null', () {
      expect(noLabel.toString(), isNot(contains('null')));
    });

    test('distanceTo — same point is ~0', () {
      expect(bhubaneswar.distanceTo(bhubaneswar), closeTo(0, 0.01));
    });

    test('distanceTo — Bhubaneswar to Delhi is ~1300km', () {
      final dist = bhubaneswar.distanceTo(different);
      expect(dist, greaterThan(1200));
      expect(dist, lessThan(1500));
    });
  });
}
