/// Domain model for the persistent incident workflow.
///
/// Mirrors the contract in `docs/api/incidents.md` (and
/// `backend/app/domain/incidents.py`): an incident is created from an eligible
/// fire alert or citizen report, then progressed through response states by a
/// responding role. History is append-only; the state machine is fixed.
///
/// The transition table is duplicated here on purpose. The server is the
/// authority — it rejects anything invalid with 409 — but a responder console
/// should never offer a button that cannot work, so the client mirrors the
/// rules to decide what to show, and still treats the server's answer as final.
library;

// --- enums, with their wire spellings kept explicit ---

enum IncidentSourceType { alert, report, publishedAlert }

enum IncidentStatus {
  reported,
  assigned,
  acknowledged,
  enRoute,
  onScene,
  resolved,
  cancelled;

  /// Terminal states accept no further transitions (cancellation is a state,
  /// not a delete — the API has no delete).
  bool get isTerminal => this == IncidentStatus.resolved || this == IncidentStatus.cancelled;
}

enum ResponderRole { fireDepartment, pollutionControl }

enum IncidentEventType { created, assigned, reassigned, transition, delivered }

const Map<IncidentStatus, String> _statusToWire = {
  IncidentStatus.reported: 'reported',
  IncidentStatus.assigned: 'assigned',
  IncidentStatus.acknowledged: 'acknowledged',
  IncidentStatus.enRoute: 'en_route',
  IncidentStatus.onScene: 'on_scene',
  IncidentStatus.resolved: 'resolved',
  IncidentStatus.cancelled: 'cancelled',
};

const Map<ResponderRole, String> _roleToWire = {
  ResponderRole.fireDepartment: 'fire_department',
  ResponderRole.pollutionControl: 'pollution_control',
};

const Map<IncidentSourceType, String> _sourceToWire = {
  IncidentSourceType.alert: 'alert',
  IncidentSourceType.report: 'report',
  IncidentSourceType.publishedAlert: 'published_alert',
};

String wireOfStatus(IncidentStatus value) => _statusToWire[value]!;

String wireOfRole(ResponderRole value) => _roleToWire[value]!;

String wireOfSource(IncidentSourceType value) => _sourceToWire[value]!;

IncidentStatus? statusFromWire(String? value) {
  for (final entry in _statusToWire.entries) {
    if (entry.value == value) return entry.key;
  }
  // An unknown status is not invented as `reported`: the console shows it as
  // unknown rather than mislabelling it.
  return null;
}

ResponderRole? roleFromWire(String? value) {
  for (final entry in _roleToWire.entries) {
    if (entry.value == value) return entry.key;
  }
  return null;
}

IncidentSourceType? sourceTypeFromWire(String? value) {
  for (final entry in _sourceToWire.entries) {
    if (entry.value == value) return entry.key;
  }
  return null;
}

IncidentEventType? eventTypeFromWire(String? value) {
  switch (value) {
    case 'created':
      return IncidentEventType.created;
    case 'assigned':
      return IncidentEventType.assigned;
    case 'reassigned':
      return IncidentEventType.reassigned;
    case 'transition':
      return IncidentEventType.transition;
    case 'delivered':
      return IncidentEventType.delivered;
  }
  return null;
}

// --- the state machine ---

/// Every transition the platform allows, exactly as the backend defines it.
///
/// `assigned` appears as a target of `reported` so this is a complete
/// description of the machine, but the generic transition route refuses it —
/// assignment has its own endpoint. Use [responsiveTransitionsFrom] for the
/// buttons a responder may actually press.
const Map<IncidentStatus, Set<IncidentStatus>> allowedTransitions = {
  IncidentStatus.reported: {IncidentStatus.assigned, IncidentStatus.cancelled},
  IncidentStatus.assigned: {IncidentStatus.acknowledged, IncidentStatus.cancelled},
  IncidentStatus.acknowledged: {IncidentStatus.enRoute, IncidentStatus.cancelled},
  IncidentStatus.enRoute: {IncidentStatus.onScene, IncidentStatus.cancelled},
  IncidentStatus.onScene: {IncidentStatus.resolved, IncidentStatus.cancelled},
  IncidentStatus.resolved: <IncidentStatus>{},
  IncidentStatus.cancelled: <IncidentStatus>{},
};

/// The subset reachable through `POST /incidents/{id}/transitions`.
Set<IncidentStatus> responsiveTransitionsFrom(IncidentStatus from) {
  final allowed = allowedTransitions[from] ?? const <IncidentStatus>{};
  return allowed.where((status) => status != IncidentStatus.assigned).toSet();
}

bool isTransitionAllowed(IncidentStatus from, IncidentStatus to) =>
    (allowedTransitions[from] ?? const <IncidentStatus>{}).contains(to);

/// One button a responder can press.
class ResponseAction {
  const ResponseAction({required this.label, this.target});

  /// Button text. Deliberately the responder's verb ("Mark en route"), not the
  /// wire state ("en_route").
  final String label;

  /// The status this action transitions to, or null for assignment (which has
  /// its own endpoint and takes an assignee).
  final IncidentStatus? target;

  bool get isAssign => target == null;
}

/// The response journey for a fire-department incident, in order.
///
/// Derived from the transition table rather than hard-coded per screen, so the
/// buttons and the state machine cannot disagree.
List<ResponseAction> actionsFor(IncidentStatus status) {
  if (status.isTerminal) return const [];
  final actions = <ResponseAction>[];
  if (status == IncidentStatus.reported) {
    actions.add(const ResponseAction(label: 'Assign to a unit'));
  }
  for (final target in _orderedStatuses) {
    if (!responsiveTransitionsFrom(status).contains(target)) continue;
    actions.add(ResponseAction(label: _actionLabels[target]!, target: target));
  }
  return actions;
}

/// Journey order, so buttons read in the order a response happens rather than
/// in enum order.
const List<IncidentStatus> _orderedStatuses = [
  IncidentStatus.acknowledged,
  IncidentStatus.enRoute,
  IncidentStatus.onScene,
  IncidentStatus.resolved,
  IncidentStatus.cancelled,
];

const Map<IncidentStatus, String> _actionLabels = {
  IncidentStatus.acknowledged: 'Acknowledge',
  IncidentStatus.enRoute: 'Mark en route',
  IncidentStatus.onScene: 'Mark on scene',
  IncidentStatus.resolved: 'Mark resolved',
  IncidentStatus.cancelled: 'Cancel incident',
};

// --- wire models ---

class Incident {
  const Incident({
    required this.id,
    required this.sourceType,
    this.sourceId,
    this.sourceRef,
    this.sourceSynthetic = false,
    required this.status,
    required this.responderRole,
    required this.severity,
    required this.latitude,
    required this.longitude,
    required this.evidenceReportIds,
    required this.createdAt,
    required this.updatedAt,
    this.jurisdiction,
    this.h3Cell,
    this.linkedPredictionRunId,
    this.assignee,
    this.resolvedAt,
  });

  final int id;
  final IncidentSourceType? sourceType;
  final int? sourceId;
  final String? sourceRef;
  final bool sourceSynthetic;
  final IncidentStatus? status;
  final ResponderRole? responderRole;
  final String severity;
  final String? jurisdiction;
  final double latitude;
  final double longitude;
  final String? h3Cell;
  final String? linkedPredictionRunId;
  final List<int> evidenceReportIds;
  final String? assignee;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime? resolvedAt;

  factory Incident.fromJson(Map<String, dynamic> json) {
    final rawEvidence = json['evidence_report_ids'];
    return Incident(
      id: (json['id'] as num).toInt(),
      sourceType: sourceTypeFromWire(json['source_type'] as String?),
      sourceId: (json['source_id'] as num?)?.toInt(),
      sourceRef: json['source_ref'] as String?,
      sourceSynthetic: json['source_synthetic'] as bool? ?? false,
      status: statusFromWire(json['status'] as String?),
      responderRole: roleFromWire(json['responder_role'] as String?),
      severity: (json['severity'] as String?) ?? 'unknown',
      jurisdiction: json['jurisdiction'] as String?,
      latitude: (json['latitude'] as num).toDouble(),
      longitude: (json['longitude'] as num).toDouble(),
      h3Cell: json['h3_cell'] as String?,
      linkedPredictionRunId: json['linked_prediction_run_id'] as String?,
      evidenceReportIds: rawEvidence is List
          ? rawEvidence.map((value) => (value as num).toInt()).toList()
          : const <int>[],
      assignee: json['assignee'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String),
      updatedAt: DateTime.parse(json['updated_at'] as String),
      resolvedAt: json['resolved_at'] == null
          ? null
          : DateTime.parse(json['resolved_at'] as String),
    );
  }

  /// True when this incident belongs to the fire department. The console only
  /// offers fire-department actions, so a pollution-control incident is shown
  /// as out of scope rather than actionable.
  bool get isFireDepartment => responderRole == ResponderRole.fireDepartment;
}

class PublishedAlert {
  const PublishedAlert({
    required this.alertId,
    required this.h3Cell,
    required this.severity,
    required this.message,
    required this.forecastHours,
    required this.forecastPm25,
  });

  final String alertId;
  final String h3Cell;
  final String severity;
  final String message;
  final double forecastHours;
  final double? forecastPm25;

  factory PublishedAlert.fromJson(Map<String, dynamic> json) => PublishedAlert(
        alertId: json['alert_id'] as String,
        h3Cell: json['h3_cell'] as String,
        severity: json['severity'] as String,
        message: json['message'] as String,
        forecastHours: (json['forecast_hours'] as num).toDouble(),
        forecastPm25: (json['forecast_pm25'] as num?)?.toDouble(),
      );
}

class IncidentDelivery {
  const IncidentDelivery({required this.incidentId, required this.status});

  final int incidentId;
  final String status;

  factory IncidentDelivery.fromJson(Map<String, dynamic> json) => IncidentDelivery(
        incidentId: (json['incident_id'] as num).toInt(),
        status: json['status'] as String,
      );
}

class IncidentInboxItem {
  const IncidentInboxItem({required this.delivery, required this.incident, required this.isOpen});

  final IncidentDelivery delivery;
  final Incident incident;
  final bool isOpen;

  factory IncidentInboxItem.fromJson(Map<String, dynamic> json) => IncidentInboxItem(
        delivery: IncidentDelivery.fromJson(json['delivery'] as Map<String, dynamic>),
        incident: Incident.fromJson(json['incident'] as Map<String, dynamic>),
        isOpen: json['is_open'] as bool? ?? false,
      );
}

class IncidentEvent {
  const IncidentEvent({
    required this.id,
    required this.incidentId,
    required this.eventType,
    required this.createdAt,
    this.fromStatus,
    this.toStatus,
    this.role,
    this.actor,
    this.note,
  });

  final int id;
  final int incidentId;
  final IncidentEventType? eventType;
  final IncidentStatus? fromStatus;
  final IncidentStatus? toStatus;
  final ResponderRole? role;
  final String? actor;
  final String? note;
  final DateTime createdAt;

  factory IncidentEvent.fromJson(Map<String, dynamic> json) => IncidentEvent(
        id: (json['id'] as num).toInt(),
        incidentId: (json['incident_id'] as num).toInt(),
        eventType: eventTypeFromWire(json['event_type'] as String?),
        fromStatus: statusFromWire(json['from_status'] as String?),
        toStatus: statusFromWire(json['to_status'] as String?),
        role: roleFromWire(json['role'] as String?),
        actor: json['actor'] as String?,
        note: json['note'] as String?,
        createdAt: DateTime.parse(json['created_at'] as String),
      );
}

/// A citizen fire report, used as the evidence behind an incident.
///
/// Incidents carry `evidence_report_ids`; the reports themselves live at
/// `GET /api/v1/reports`. The console joins the two so a responder sees what
/// was actually reported, not just an id.
class CitizenReport {
  const CitizenReport({
    required this.id,
    required this.h3Cell,
    required this.kind,
    required this.smokeIntensity,
    required this.durationHours,
    required this.reportedAt,
    this.notes,
  });

  final int id;
  final String h3Cell;
  final String kind;
  final int smokeIntensity;
  final double durationHours;
  final String? notes;
  final DateTime reportedAt;

  factory CitizenReport.fromJson(Map<String, dynamic> json) => CitizenReport(
        id: (json['id'] as num).toInt(),
        h3Cell: (json['h3_cell'] as String?) ?? '',
        kind: (json['kind'] as String?) ?? 'unknown',
        smokeIntensity: (json['smoke_intensity'] as num?)?.toInt() ?? 0,
        durationHours: (json['duration_hours'] as num?)?.toDouble() ?? 0,
        notes: json['notes'] as String?,
        reportedAt: DateTime.parse(json['reported_at'] as String),
      );
}
