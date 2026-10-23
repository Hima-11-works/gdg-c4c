import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:fire_dept_simulator/src/api.dart';
import 'package:fire_dept_simulator/src/config.dart';
import 'package:fire_dept_simulator/src/models.dart';
import 'package:fire_dept_simulator/src/screens/incidents_screen.dart';

class _FakeIncidentApi extends IncidentApi {
  _FakeIncidentApi({
    required this.result,
    this.firstRequest,
    required String baseUrl,
  }) : super(baseUrl: baseUrl);

  final Incident result;
  final Completer<List<Incident>>? firstRequest;
  final requestedRoles = <ResponderRole?>[];
  int requestCount = 0;

  @override
  bool get canWrite => false;

  @override
  Future<List<Incident>> listIncidents({
    IncidentStatus? status,
    ResponderRole? role,
    bool archive = false,
    int limit = IncidentApi.incidentPageSize,
    int offset = 0,
  }) {
    requestCount++;
    requestedRoles.add(role);
    if (requestCount == 1 && firstRequest != null) {
      return firstRequest!.future;
    }
    return Future.value([result]);
  }

  @override
  Future<List<IncidentInboxItem>> listInbox(ResponderRole role) async => const [];

  @override
  Future<List<Map<String, dynamic>>> listHotspotEvents() async => const [];

  @override
  Future<List<FireDetection>> listFireDetections({double sinceHours = 6}) async => const [];
}

Incident _incident(int id, ResponderRole role) => Incident(
      id: id,
      sourceType: IncidentSourceType.report,
      sourceId: id,
      status: IncidentStatus.reported,
      responderRole: role,
      severity: 'high',
      latitude: 28.6,
      longitude: 77.2,
      evidenceReportIds: const [],
      createdAt: DateTime.utc(2026, 10, 21),
      updatedAt: DateTime.utc(2026, 10, 21),
    );

SimulatorConfig _config(String baseUrl) => SimulatorConfig(
      baseUrl: baseUrl,
      apiKey: '',
      role: ResponderRole.fireDepartment,
      actorId: 'fire-unit-7',
    );

void main() {
  testWidgets('filter change queues a fresh request and drops the old result',
      (tester) async {
    final firstRequest = Completer<List<Incident>>();
    final api = _FakeIncidentApi(
      baseUrl: 'https://api.example.test',
      firstRequest: firstRequest,
      result: _incident(2, ResponderRole.pollutionControl),
    );
    addTearDown(api.close);

    await tester.pumpWidget(MaterialApp(
      home: IncidentsScreen(
        api: api,
        config: _config('https://api.example.test'),
        onOpenSettings: () {},
      ),
    ));
    await tester.pump();

    await tester.tap(find.byType(Switch).first);
    await tester.pump();
    firstRequest.complete([_incident(1, ResponderRole.fireDepartment)]);
    await tester.pumpAndSettle();

    expect(api.requestedRoles, [ResponderRole.fireDepartment, null]);
    expect(find.text('#1'), findsNothing);
    expect(find.text('#2'), findsOneWidget);
  });

  testWidgets('backend change queues a request against the new API',
      (tester) async {
    final firstRequest = Completer<List<Incident>>();
    final oldApi = _FakeIncidentApi(
      baseUrl: 'https://old.example.test',
      firstRequest: firstRequest,
      result: _incident(1, ResponderRole.fireDepartment),
    );
    final newApi = _FakeIncidentApi(
      baseUrl: 'https://new.example.test',
      result: _incident(2, ResponderRole.fireDepartment),
    );
    addTearDown(oldApi.close);
    addTearDown(newApi.close);

    await tester.pumpWidget(MaterialApp(
      home: IncidentsScreen(
        api: oldApi,
        config: _config('https://old.example.test'),
        onOpenSettings: () {},
      ),
    ));
    await tester.pump();

    await tester.pumpWidget(MaterialApp(
      home: IncidentsScreen(
        api: newApi,
        config: _config('https://new.example.test'),
        onOpenSettings: () {},
      ),
    ));
    await tester.pump();
    firstRequest.complete([_incident(1, ResponderRole.fireDepartment)]);
    await tester.pumpAndSettle();

    expect(oldApi.requestCount, 1);
    expect(newApi.requestCount, 1);
    expect(find.text('#1'), findsNothing);
    expect(find.text('#2'), findsOneWidget);
  });
}
