import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/domain/pm25_aqi.dart';

void main() {
  group('pm25ToCpcbAqi', () {
    test('maps the band boundaries', () {
      expect(pm25ToCpcbAqi(0), 0);
      expect(pm25ToCpcbAqi(30), 50);
      expect(pm25ToCpcbAqi(60), 100);
      expect(pm25ToCpcbAqi(90), 200);
      expect(pm25ToCpcbAqi(120), 300);
      expect(pm25ToCpcbAqi(250), 400);
      expect(pm25ToCpcbAqi(500), 500);
    });

    test('interpolates within a band', () {
      expect(pm25ToCpcbAqi(45), 75); // 30–60 → 50–100
      expect(pm25ToCpcbAqi(75), 150); // 60–90 → 100–200
    });

    test('clamps above the scale', () {
      expect(pm25ToCpcbAqi(1000), 500);
    });

    test('treats negative concentrations as zero', () {
      expect(pm25ToCpcbAqi(-5), 0);
    });
  });

  group('pm25ToCategory', () {
    test('follows the derived AQI', () {
      expect(pm25ToCategory(45), CpcbCategory.satisfactory);
      expect(pm25ToCategory(90), CpcbCategory.moderate);
      expect(pm25ToCategory(150), CpcbCategory.veryPoor);
    });
  });
}
