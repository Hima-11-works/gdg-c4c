import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/cpcb_category.dart';

void main() {
  group('CpcbCategory', () {
    test('classifies Good (0–50)', () {
      expect(CpcbCategory.fromAqi(0), CpcbCategory.good);
      expect(CpcbCategory.fromAqi(50), CpcbCategory.good);
    });

    test('classifies Satisfactory (51–100)', () {
      expect(CpcbCategory.fromAqi(51), CpcbCategory.satisfactory);
      expect(CpcbCategory.fromAqi(100), CpcbCategory.satisfactory);
    });

    test('classifies Moderate (101–200)', () {
      expect(CpcbCategory.fromAqi(101), CpcbCategory.moderate);
      expect(CpcbCategory.fromAqi(200), CpcbCategory.moderate);
    });

    test('classifies Poor (201–300)', () {
      expect(CpcbCategory.fromAqi(201), CpcbCategory.poor);
      expect(CpcbCategory.fromAqi(300), CpcbCategory.poor);
    });

    test('classifies Very Poor (301–400)', () {
      expect(CpcbCategory.fromAqi(301), CpcbCategory.veryPoor);
      expect(CpcbCategory.fromAqi(400), CpcbCategory.veryPoor);
    });

    test('classifies Severe (401+)', () {
      expect(CpcbCategory.fromAqi(401), CpcbCategory.severe);
      expect(CpcbCategory.fromAqi(500), CpcbCategory.severe);
      expect(CpcbCategory.fromAqi(999), CpcbCategory.severe);
    });
  });
}
