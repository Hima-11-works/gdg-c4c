import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  group('DataFreshness', () {
    final now = DateTime(2026, 9, 17, 10, 0);
    final fresh = DataFreshness(retrievedAt: now, quality: DataQuality.full);
    final stale = DataFreshness(retrievedAt: now, quality: DataQuality.stale);

    test('equality', () {
      final same = DataFreshness(retrievedAt: now, quality: DataQuality.full);
      expect(fresh, equals(same));
      expect(fresh.hashCode, equals(same.hashCode));
      expect(fresh, isNot(equals(stale)));
    });

    test('isStale', () {
      expect(fresh.isStale, isFalse);
      expect(stale.isStale, isTrue);
    });

    test('timestamps older than two hours are effectively stale', () {
      expect(fresh.isStaleAt(now), isFalse);
      expect(
        DataFreshness(
          retrievedAt: now.subtract(const Duration(hours: 3)),
          quality: DataQuality.full,
        ).isStaleAt(now),
        isTrue,
      );
      expect(
        DataFreshness(
          retrievedAt: now.add(const Duration(hours: 3)),
          quality: DataQuality.full,
        ).isStaleAt(now),
        isTrue,
      );
    });
  });

  group('PollutionEvent', () {
    final arrival = DateTime(2026, 9, 17, 12, 0);
    final event = PollutionEvent(
      id: 'evt-1',
      sourceArea: 'Industrial Belt',
      expectedArrivalAt: arrival,
      peakAqiEstimate: 320,
      confidence: 0.8,
      description: 'Heavy plume from industrial area',
    );
    final same = PollutionEvent(
      id: 'evt-1',
      sourceArea: 'Industrial Belt',
      expectedArrivalAt: arrival,
      peakAqiEstimate: 320,
      confidence: 0.8,
      description: 'Heavy plume from industrial area',
    );

    test('equality', () {
      expect(event, equals(same));
      expect(event.hashCode, equals(same.hashCode));
    });
  });

  group('NearbyArea', () {
    const area = NearbyArea(
      location: LocationPoint(latitude: 20.3, longitude: 85.8),
      name: 'Cuttack',
      aqiNow: 95,
      forecast: [],
      trend: AreaTrend.improving,
      distanceKm: 25.0,
      confidence: 0.9,
    );
    const same = NearbyArea(
      location: LocationPoint(latitude: 20.3, longitude: 85.8),
      name: 'Cuttack',
      aqiNow: 95,
      forecast: [],
      trend: AreaTrend.improving,
      distanceKm: 25.0,
      confidence: 0.9,
    );

    test('equality', () {
      expect(area, equals(same));
      expect(area.hashCode, equals(same.hashCode));
    });
  });

  group('AlertDecision', () {
    test('equality', () {
      const a = AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.currentThreshold,
        currentAqi: 250,
        confidence: 0.9,
        messageContext: 'AQI is Poor',
        dedupKey: 'current_poor',
        guidance: 'Consider reducing outdoor exposure',
      );
      const b = AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.currentThreshold,
        currentAqi: 250,
        confidence: 0.9,
        messageContext: 'AQI is Poor',
        dedupKey: 'current_poor',
        guidance: 'Consider reducing outdoor exposure',
      );
      expect(a, equals(b));
      expect(a.hashCode, equals(b.hashCode));
    });

    test('none sentinel', () {
      expect(AlertDecision.none.shouldAlert, isFalse);
    });
  });

  group('DedupEntry', () {
    test('equality', () {
      final t = DateTime(2026, 9, 17);
      final a = DedupEntry(key: 'k1', lastAlertedAt: t);
      final b = DedupEntry(key: 'k1', lastAlertedAt: t);
      expect(a, equals(b));
    });
  });
}
