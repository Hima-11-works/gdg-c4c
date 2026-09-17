import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/cpcb_category.dart';
import 'package:air_health_flutter/domain/models/air_quality_reading.dart';

void main() {
  group('CpcbCategory', () {
    test('classifies all bands correctly', () {
      expect(CpcbCategory.fromAqi(0), CpcbCategory.good);
      expect(CpcbCategory.fromAqi(50), CpcbCategory.good);
      expect(CpcbCategory.fromAqi(51), CpcbCategory.satisfactory);
      expect(CpcbCategory.fromAqi(100), CpcbCategory.satisfactory);
      expect(CpcbCategory.fromAqi(101), CpcbCategory.moderate);
      expect(CpcbCategory.fromAqi(200), CpcbCategory.moderate);
      expect(CpcbCategory.fromAqi(201), CpcbCategory.poor);
      expect(CpcbCategory.fromAqi(300), CpcbCategory.poor);
      expect(CpcbCategory.fromAqi(301), CpcbCategory.veryPoor);
      expect(CpcbCategory.fromAqi(400), CpcbCategory.veryPoor);
      expect(CpcbCategory.fromAqi(401), CpcbCategory.severe);
      expect(CpcbCategory.fromAqi(500), CpcbCategory.severe);
      expect(CpcbCategory.fromAqi(999), CpcbCategory.severe);
    });

    test('labels are non-empty', () {
      for (final cat in CpcbCategory.values) {
        expect(cat.label, isNotEmpty);
      }
    });
  });

  group('AirQualityReading', () {
    final now = DateTime(2026, 9, 17, 10, 0);
    final reading = AirQualityReading(
      aqiCpcb: 142,
      pm25: 82.5,
      primaryPollutant: 'PM2.5',
      category: CpcbCategory.moderate,
      recordedAt: now,
    );
    final same = AirQualityReading(
      aqiCpcb: 142,
      pm25: 82.5,
      primaryPollutant: 'PM2.5',
      category: CpcbCategory.moderate,
      recordedAt: now,
    );
    final different = AirQualityReading(
      aqiCpcb: 250,
      pm25: 150,
      primaryPollutant: 'PM2.5',
      category: CpcbCategory.poor,
      recordedAt: now,
    );

    test('equality', () {
      expect(reading, equals(same));
      expect(reading.hashCode, equals(same.hashCode));
      expect(reading, isNot(equals(different)));
    });

    test('isPoorOrWorse', () {
      expect(reading.isPoorOrWorse, isFalse); // moderate
      expect(different.isPoorOrWorse, isTrue); // poor
    });

    test('category matches aqiCpcb', () {
      expect(reading.category, CpcbCategory.moderate);
      expect(different.category, CpcbCategory.poor);
    });
  });
}
