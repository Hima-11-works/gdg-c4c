/// Presentation helpers: labels and time. No widgets, no I/O.
library;

import 'models.dart';

String statusLabel(IncidentStatus? status) {
  switch (status) {
    case IncidentStatus.reported:
      return 'Reported';
    case IncidentStatus.assigned:
      return 'Assigned';
    case IncidentStatus.acknowledged:
      return 'Acknowledged';
    case IncidentStatus.enRoute:
      return 'En route';
    case IncidentStatus.onScene:
      return 'On scene';
    case IncidentStatus.resolved:
      return 'Resolved';
    case IncidentStatus.cancelled:
      return 'Cancelled';
    case null:
      // The model refuses to guess at an unrecognised status, so neither does
      // the UI — showing "Reported" for something unknown would be a lie.
      return 'Unrecognised status';
  }
}

String roleLabel(ResponderRole? role) {
  switch (role) {
    case ResponderRole.fireDepartment:
      return 'Fire department';
    case ResponderRole.pollutionControl:
      return 'Pollution control';
    case null:
      return 'Unknown role';
  }
}

String sourceLabel(IncidentSourceType? source) {
  switch (source) {
    case IncidentSourceType.alert:
      return 'Fire alert';
      case IncidentSourceType.report:
        return 'Citizen report';
      case IncidentSourceType.publishedAlert:
        return 'Published PM2.5 alert';
    case null:
      return 'Unknown source';
  }
}

String severityLabel(String severity) {
  if (severity.isEmpty) return 'Unknown severity';
  return severity[0].toUpperCase() + severity.substring(1);
}

String eventLabel(IncidentEventType? type) {
  switch (type) {
    case IncidentEventType.created:
      return 'Created';
    case IncidentEventType.assigned:
      return 'Assigned';
    case IncidentEventType.reassigned:
      return 'Reassigned';
    case IncidentEventType.transition:
      return 'Status change';
    case IncidentEventType.delivered:
      return 'Simulated inbox delivery';
    case null:
      return 'Event';
  }
}

/// "just now" / "12 min ago" / "3h ago" / "2d ago", always in the past tense
/// sense the history uses. A future timestamp reads as "just now" rather than
/// as a negative age.
String relativeTime(DateTime when, {DateTime? now}) {
  final reference = now ?? DateTime.now();
  final minutes = reference.difference(when).inMinutes;
  if (minutes < 1) return 'just now';
  if (minutes < 60) return '$minutes min ago';
  final hours = minutes ~/ 60;
  if (hours < 48) return '${hours}h ago';
  return '${hours ~/ 24}d ago';
}

/// Fixed 4 dp, matching how the citizen report form shows a dropped pin.
String formatCoordinates(double latitude, double longitude) =>
    '${latitude.toStringAsFixed(4)}, ${longitude.toStringAsFixed(4)}';

String formatDuration(double hours) {
  if (hours <= 0) return 'just started';
  if (hours < 1) return '${(hours * 60).round()} min';
  return '${hours.toStringAsFixed(hours == hours.roundToDouble() ? 0 : 1)}h';
}
