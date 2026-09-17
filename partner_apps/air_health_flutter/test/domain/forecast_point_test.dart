import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/forecast_point.dart';
import 'package:air_health_flutter/domain/models/cpcb_category.dart';

void main() {
  group('ForecastPoint', () {
    final t = DateTime(2026, 9, 17, 14, 0);
    final point = ForecastPoint(at: t, aqiCpcb: 180, pm25: 100, confidence: 0.85);
    final same = ForecastPoint(at: t, aqiCpcb: 180, pm25: 100, confidence: 0.85);
    final different = ForecastPoint(at: t, aqiCpcb: 250, confidence: 0.9);

    test('equality', () {
      expect(point, equals(same));
      expect(point.hashCode, equals(same.hashCode));
      expect(point, isNot(equals(different)));
    });

    test('category is derived from aqiCpcb', () {
      expect(point.category, CpcbCategory.moderate); // 180
      expect(different.category, CpcbCategory.poor); // 250
    });
  });
}
