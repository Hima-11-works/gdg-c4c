import 'package:flutter_test/flutter_test.dart';

import 'package:fire_dept_simulator/src/fmt.dart';
import 'package:fire_dept_simulator/src/models.dart';

void main() {
  group('transition table', () {
    test('mirrors the documented state machine', () {
      expect(isTransitionAllowed(IncidentStatus.reported, IncidentStatus.assigned), isTrue);
      expect(isTransitionAllowed(IncidentStatus.assigned, IncidentStatus.acknowledged), isTrue);
      expect(isTransitionAllowed(IncidentStatus.acknowledged, IncidentStatus.enRoute), isTrue);
      expect(isTransitionAllowed(IncidentStatus.enRoute, IncidentStatus.onScene), isTrue);
      expect(isTransitionAllowed(IncidentStatus.onScene, IncidentStatus.resolved), isTrue);
    });

    test('refuses jumps the machine does not allow', () {
      expect(isTransitionAllowed(IncidentStatus.assigned, IncidentStatus.resolved), isFalse);
      expect(isTransitionAllowed(IncidentStatus.reported, IncidentStatus.onScene), isFalse);
      expect(isTransitionAllowed(IncidentStatus.acknowledged, IncidentStatus.onScene), isFalse);
    });

    test('terminal states allow nothing', () {
      expect(responsiveTransitionsFrom(IncidentStatus.resolved), isEmpty);
      expect(responsiveTransitionsFrom(IncidentStatus.cancelled), isEmpty);
      expect(isTransitionAllowed(IncidentStatus.resolved, IncidentStatus.enRoute), isFalse);
    });

    test('cancellation is reachable from every non-terminal state', () {
      for (final status in IncidentStatus.values) {
        if (status.isTerminal) continue;
        expect(
          responsiveTransitionsFrom(status).contains(IncidentStatus.cancelled),
          isTrue,
          reason: '${wireOfStatus(status)} should be cancellable',
        );
      }
    });

    test('the transition route never offers assignment', () {
      for (final status in IncidentStatus.values) {
        expect(responsiveTransitionsFrom(status).contains(IncidentStatus.assigned), isFalse);
      }
    });

    test('isTerminal is only true for resolved and cancelled', () {
      for (final status in IncidentStatus.values) {
        expect(status.isTerminal, status == IncidentStatus.resolved || status == IncidentStatus.cancelled);
      }
    });
  });

  group('actions', () {
    test('reported offers assignment first, plus cancel', () {
      final actions = actionsFor(IncidentStatus.reported);
      expect(actions, isNotEmpty);
      expect(actions.first.isAssign, isTrue);
      expect(actions.map((a) => a.target), contains(IncidentStatus.cancelled));
    });

    test('the response journey is ordered', () {
      expect(actionsFor(IncidentStatus.assigned).first.target, IncidentStatus.acknowledged);
      expect(actionsFor(IncidentStatus.acknowledged).first.target, IncidentStatus.enRoute);
      expect(actionsFor(IncidentStatus.enRoute).first.target, IncidentStatus.onScene);
      expect(actionsFor(IncidentStatus.onScene).first.target, IncidentStatus.resolved);
    });

    test('no action is offered for a terminal state', () {
      expect(actionsFor(IncidentStatus.resolved), isEmpty);
      expect(actionsFor(IncidentStatus.cancelled), isEmpty);
    });

    test('assignment is never offered as a transition target', () {
      for (final status in IncidentStatus.values) {
        for (final action in actionsFor(status)) {
          expect(action.target, isNot(IncidentStatus.assigned));
        }
      }
    });
  });

  group('wire mapping', () {
    test('statuses map both ways', () {
      expect(statusFromWire('en_route'), IncidentStatus.enRoute);
      expect(statusFromWire('on_scene'), IncidentStatus.onScene);
      expect(statusFromWire('cancelled'), IncidentStatus.cancelled);
      expect(wireOfStatus(IncidentStatus.onScene), 'on_scene');
      expect(wireOfStatus(IncidentStatus.enRoute), 'en_route');
    });

    test('roles and sources map', () {
      expect(wireOfRole(ResponderRole.fireDepartment), 'fire_department');
      expect(wireOfRole(ResponderRole.pollutionControl), 'pollution_control');
      expect(roleFromWire('pollution_control'), ResponderRole.pollutionControl);
      expect(wireOfSource(IncidentSourceType.report), 'report');
      expect(sourceTypeFromWire('alert'), IncidentSourceType.alert);
    });

    test('an unrecognised value is not invented', () {
      expect(statusFromWire('teleported'), isNull);
      expect(roleFromWire('warden'), isNull);
      expect(sourceTypeFromWire('rumour'), isNull);
    });
  });

  group('Incident.fromJson', () {
    final json = <String, dynamic>{
      'id': 5,
      'source_type': 'alert',
      'source_id': 17,
      'status': 'on_scene',
      'responder_role': 'fire_department',
      'severity': 'critical',
      'jurisdiction': 'Delhi',
      'latitude': 28.61,
      'longitude': 77.21,
      'h3_cell': '8861892e0dfffff',
      'linked_prediction_run_id': 'demo-run-1',
      'evidence_report_ids': [3, 4],
      'assignee': 'unit-12',
      'created_at': '2026-09-23T09:00:00Z',
      'updated_at': '2026-09-23T09:20:00Z',
      'resolved_at': null,
    };

    test('parses every documented field', () {
      final incident = Incident.fromJson(json);
      expect(incident.id, 5);
      expect(incident.sourceType, IncidentSourceType.alert);
      expect(incident.status, IncidentStatus.onScene);
      expect(incident.responderRole, ResponderRole.fireDepartment);
      expect(incident.severity, 'critical');
      expect(incident.jurisdiction, 'Delhi');
      expect(incident.evidenceReportIds, [3, 4]);
      expect(incident.assignee, 'unit-12');
      expect(incident.resolvedAt, isNull);
      expect(incident.createdAt.isUtc, isTrue);
      expect(incident.isFireDepartment, isTrue);
    });

    test('a pollution-control incident is not fire-department work', () {
      final incident = Incident.fromJson({...json, 'responder_role': 'pollution_control'});
      expect(incident.isFireDepartment, isFalse);
    });

    test('a missing evidence list reads as empty, not as an error', () {
      final withoutEvidence = Map<String, dynamic>.from(json)..remove('evidence_report_ids');
      expect(Incident.fromJson(withoutEvidence).evidenceReportIds, isEmpty);
    });

    test('an unknown status stays unknown rather than defaulting to reported', () {
      final incident = Incident.fromJson({...json, 'status': 'teleported'});
      expect(incident.status, isNull);
      expect(statusLabel(incident.status), 'Unrecognised status');
    });
  });

  group('IncidentEvent.fromJson', () {
    test('parses a transition event', () {
      final event = IncidentEvent.fromJson(<String, dynamic>{
        'id': 31,
        'incident_id': 5,
        'event_type': 'transition',
        'from_status': 'assigned',
        'to_status': 'acknowledged',
        'role': 'fire_department',
        'actor': 'unit-12',
        'note': 'Unit dispatched',
        'created_at': '2026-09-23T09:20:00Z',
      });
      expect(event.eventType, IncidentEventType.transition);
      expect(event.fromStatus, IncidentStatus.assigned);
      expect(event.toStatus, IncidentStatus.acknowledged);
      expect(event.actor, 'unit-12');
      expect(event.note, 'Unit dispatched');
    });

    test('parses a created event with no statuses', () {
      final event = IncidentEvent.fromJson(<String, dynamic>{
        'id': 1,
        'incident_id': 5,
        'event_type': 'created',
        'from_status': null,
        'to_status': 'reported',
        'role': 'fire_department',
        'actor': null,
        'note': null,
        'created_at': '2026-09-23T09:00:00Z',
      });
      expect(event.eventType, IncidentEventType.created);
      expect(event.fromStatus, isNull);
      expect(event.toStatus, IncidentStatus.reported);
    });
  });

  group('formatters', () {
    final now = DateTime.utc(2026, 9, 24, 9, 0);

    test('relative time reads in the past tense', () {
      expect(relativeTime(now.subtract(const Duration(seconds: 20)), now: now), 'just now');
      expect(relativeTime(now.subtract(const Duration(minutes: 12)), now: now), '12 min ago');
      expect(relativeTime(now.subtract(const Duration(hours: 3)), now: now), '3h ago');
      expect(relativeTime(now.subtract(const Duration(days: 2)), now: now), '2d ago');
    });

    test('a future timestamp does not read as a negative age', () {
      expect(relativeTime(now.add(const Duration(minutes: 5)), now: now), 'just now');
    });

    test('coordinates are fixed to four places', () {
      expect(formatCoordinates(28.6139, 77.209), '28.6139, 77.2090');
    });

    test('duration reads as the form offers it', () {
      expect(formatDuration(0), 'just started');
      expect(formatDuration(0.5), '30 min');
      expect(formatDuration(2), '2h');
      expect(formatDuration(4.5), '4.5h');
    });

    test('labels cover every status', () {
      for (final status in IncidentStatus.values) {
        expect(statusLabel(status), isNotEmpty);
      }
    });
  });
}
