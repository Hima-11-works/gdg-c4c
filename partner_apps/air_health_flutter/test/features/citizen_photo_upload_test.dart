import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/storage/citizen_reports_store.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('Citizen Photo Upload & Report Store', () {
    setUp(() {
      SharedPreferences.setMockInitialValues({});
    });
    test('creates report draft with region, photo bytes, and filename', () {
      final fakePhoto = Uint8List.fromList([1, 2, 3, 4, 5]);

      final draft = FireReportDraft.create(
        latitude: 26.8467,
        longitude: 80.9462,
        kind: FireKind.buildingFire,
        smokeIntensity: 4,
        durationHours: 1.0,
        region: 'Lucknow (LKO)',
        photoBytes: fakePhoto,
        photoFilename: 'building_fire_lko.jpg',
        notes: 'Building fire spotted near Hazratganj with heavy black smoke',
      );

      expect(draft.kind, FireKind.buildingFire);
      expect(draft.region, 'Lucknow (LKO)');
      expect(draft.hasPhoto, isTrue);
      expect(draft.photoFilename, 'building_fire_lko.jpg');
      expect(draft.photoBytes, fakePhoto);
      expect(draft.smokeIntensity, 4);
    });

    test('stores and retrieves citizen reports locally', () async {
      final store = CitizenReportsStore();
      final photo = Uint8List.fromList([10, 20, 30, 40]);

      final report = FireReport(
        id: 12345,
        h3Cell: '8828308281fffff',
        latitude: 26.8467,
        longitude: 80.9462,
        kind: FireKind.buildingFire,
        smokeIntensity: 5,
        durationHours: 2.0,
        reportedAt: DateTime.now(),
        region: 'Lucknow (LKO)',
        photoBytes: photo,
        photoFilename: 'fire_evidence.jpg',
        notes: 'Active building fire',
      );

      await store.saveReport(report);
      final list = await store.getAllReports();

      expect(list, isNotEmpty);
      final saved = list.first;
      expect(saved.id, 12345);
      expect(saved.region, 'Lucknow (LKO)');
      expect(saved.kind, FireKind.buildingFire);
      expect(saved.hasPhoto, isTrue);
      expect(saved.photoBytes, photo);
      expect(saved.photoFilename, 'fire_evidence.jpg');
    });

    test('persists pending upload state and supports durable retry or removal',
        () async {
      final photo = Uint8List.fromList([1, 2, 3, 4]);
      final pending = FireReport(
        id: 123,
        h3Cell: '8828308281fffff',
        latitude: 26.8467,
        longitude: 80.9462,
        kind: FireKind.buildingFire,
        smokeIntensity: 4,
        durationHours: 1,
        reportedAt: DateTime.utc(2026, 9, 21, 12),
        photoBytes: photo,
        photoFilename: 'evidence.jpg',
        photoUploadPending: true,
      );

      await CitizenReportsStore().saveReport(pending);
      final afterRestart = (await CitizenReportsStore().getAllReports()).single;
      expect(afterRestart.id, pending.id);
      expect(afterRestart.photoUploadPending, isTrue);
      expect(afterRestart.photoBytes, orderedEquals(photo));

      await CitizenReportsStore()
          .saveReport(afterRestart.copyWith(photoUploadPending: false));
      final uploaded = (await CitizenReportsStore().getAllReports()).single;
      expect(uploaded.photoUploadPending, isFalse);
      expect(uploaded.hasPhoto, isTrue);

      await CitizenReportsStore().saveReport(
        uploaded.copyWith(clearPhoto: true, photoUploadPending: false),
      );
      final removed = (await CitizenReportsStore().getAllReports()).single;
      expect(removed.photoUploadPending, isFalse);
      expect(removed.hasPhoto, isFalse);
      expect(removed.photoFilename, isNull);
    });
  });
}
