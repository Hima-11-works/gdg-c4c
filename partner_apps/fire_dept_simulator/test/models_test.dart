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

    test('a published alert has no source_id, and that must not throw', () {
      // The wire shape for a published-alert incident: source_id is null and
      // the source is named by its v2: identity. A non-nullable source_id would
      // have thrown on the cast and taken the whole queue down with it.
      final incident = Incident.fromJson(<String, dynamic>{
        ...json,
        'source_type': 'published_alert',
        'source_id': null,
        'source_ref': 'v2:pred-20260924T0000Z-india:8861892e0dfffff:6',
        'source_synthetic': true,
        'responder_role': 'pollution_control',
      });
      expect(incident.sourceId, isNull);
      expect(incident.sourceRef, 'v2:pred-20260924T0000Z-india:8861892e0dfffff:6');
      expect(incident.sourceSynthetic, isTrue);
      expect(incident.isFireDepartment, isFalse);
    });

    test('missing coordinates are null, not zero', () {
      final incident = Incident.fromJson(<String, dynamic>{
        ...json,
        'latitude': null,
        'longitude': null,
      });
      expect(incident.latitude, isNull);
      expect(incident.longitude, isNull);
    });

    test('a missing source_synthetic reads as false', () {
      final withoutFlag = Map<String, dynamic>.from(json)..remove('source_synthetic');
      expect(Incident.fromJson(withoutFlag).sourceSynthetic, isFalse);
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

    test('names the acting authority, not only the role', () {
      // actor + role + jurisdiction is the whole point of the identity change:
      // "who acted", not merely "which role acted".
      final event = IncidentEvent.fromJson(<String, dynamic>{
        'id': 32,
        'incident_id': 5,
        'event_type': 'transition',
        'from_status': 'assigned',
        'to_status': 'acknowledged',
        'role': 'fire_department',
        'actor': 'engine-7',
        'actor_jurisdiction': 'Delhi',
        'note': null,
        'created_at': '2026-09-23T09:20:00Z',
      });
      expect(event.actorJurisdiction, 'Delhi');
      expect(event.actorSummary, contains('engine-7'));
      expect(event.actorSummary, contains('Delhi'));
    });

    test('a service event with no actor says so', () {
      final event = IncidentEvent.fromJson(<String, dynamic>{
        'id': 33,
        'incident_id': 5,
        'event_type': 'delivered',
        'from_status': 'assigned',
        'to_status': 'assigned',
        'role': 'fire_department',
        'actor': null,
        'actor_jurisdiction': null,
        'note': null,
        'created_at': '2026-09-23T09:20:00Z',
      });
      expect(event.actor, isNull);
      expect(event.actorSummary, 'service');
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

  group('IncidentDelivery.fromJson', () {
    final json = <String, dynamic>{
      'id': 8,
      'incident_id': 5,
      'audience_role': 'fire_department',
      'status': 'simulated',
      'assignee': 'engine-7',
      'simulated': true,
      'notification': 'none (simulated inbox only; no email, SMS, or webhook is sent)',
      'simulated_at': '2026-09-23T09:05:00Z',
      'acknowledged_at': null,
    };

    test('reads the simulation flag and the notification wording', () {
      final delivery = IncidentDelivery.fromJson(json);
      expect(delivery.simulated, isTrue);
      expect(delivery.status, 'simulated');
      expect(delivery.audienceRole, ResponderRole.fireDepartment);
      expect(delivery.assignee, 'engine-7');
      // The wording is what stops a consumer reading this as a real dispatch.
      expect(delivery.notification, startsWith('none'));
      expect(delivery.acknowledgedAt, isNull);
    });

    test('an acknowledged delivery carries the time', () {
      final delivery = IncidentDelivery.fromJson(<String, dynamic>{
        ...json,
        'status': 'acknowledged',
        'acknowledged_at': '2026-09-23T09:20:00Z',
      });
      expect(delivery.status, 'acknowledged');
      expect(delivery.acknowledgedAt, isNotNull);
    });
  });

  group('InboxItem.fromJson', () {
    test('joins a delivery to its incident and reports openness', () {
      final item = InboxItem.fromJson(<String, dynamic>{
        'delivery': {
          'id': 8,
          'incident_id': 5,
          'audience_role': 'fire_department',
          'status': 'simulated',
          'assignee': 'engine-7',
          'simulated': true,
          'notification': 'none (simulated inbox only)',
          'simulated_at': '2026-09-23T09:05:00Z',
          'acknowledged_at': null,
        },
        'incident': {
          'id': 5,
          'source_type': 'report',
          'source_id': 17,
          'status': 'assigned',
          'responder_role': 'fire_department',
          'severity': 'critical',
          'latitude': 28.61,
          'longitude': 77.21,
          'evidence_report_ids': [3],
          'created_at': '2026-09-23T09:00:00Z',
          'updated_at': '2026-09-23T09:05:00Z',
          'resolved_at': null,
        },
        'is_open': true,
      });
      expect(item.isOpen, isTrue);
      expect(item.delivery.simulated, isTrue);
      expect(item.incident.id, 5);
      expect(item.incident.evidenceReportIds, [3]);
    });
  });
}
