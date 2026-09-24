/// Shared widgets: the simulation label, status/severity chips, evidence.
library;

import 'package:flutter/material.dart';

import 'api.dart';
import 'fmt.dart';
import 'models.dart';

/// The label this whole app wears.
///
/// It is not decoration. This console looks like a dispatch tool and moves
/// incidents through response states, so every screen has to say what it is:
/// a simulator working on synthetic records, wired to nothing. It appears on
/// the list, on every detail view, and next to every action's result.
class SimulationBanner extends StatelessWidget {
  const SimulationBanner({super.key, this.dense = false});

  final bool dense;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: EdgeInsets.symmetric(
        horizontal: dense ? 10 : 14,
        vertical: dense ? 6 : 9,
      ),
      decoration: BoxDecoration(
        color: scheme.tertiaryContainer,
        border: Border(
          bottom: BorderSide(color: scheme.tertiary.withValues(alpha: 0.4)),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.science_outlined, size: dense ? 15 : 18, color: scheme.onTertiaryContainer),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'SIMULATION — synthetic records, no real dispatch. Nothing here contacts an '
              'emergency service, and no one outside this simulator is notified.',
              style: (dense ? Theme.of(context).textTheme.labelSmall : Theme.of(context).textTheme.labelMedium)
                  ?.copyWith(color: scheme.onTertiaryContainer),
            ),
          ),
        ],
      ),
    );
  }
}

/// Colour for a response status. Deliberately a calm ramp: this is a simulator,
/// and alarming red is reserved for a severity, not for a state.
Color statusColor(BuildContext context, IncidentStatus? status) {
  final scheme = Theme.of(context).colorScheme;
  switch (status) {
    case IncidentStatus.reported:
      return scheme.primary;
    case IncidentStatus.assigned:
      return scheme.tertiary;
    case IncidentStatus.acknowledged:
    case IncidentStatus.enRoute:
    case IncidentStatus.onScene:
      return Colors.orange.shade700;
    case IncidentStatus.resolved:
      return Colors.green.shade700;
    case IncidentStatus.cancelled:
      return scheme.outline;
    case null:
      return scheme.error;
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status, this.large = false});

  final IncidentStatus? status;
  final bool large;

  @override
  Widget build(BuildContext context) {
    final color = statusColor(context, status);
    return Container(
      padding: EdgeInsets.symmetric(horizontal: large ? 12 : 8, vertical: large ? 6 : 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.6)),
      ),
      child: Text(
        statusLabel(status).toUpperCase(),
        style: (large ? Theme.of(context).textTheme.labelLarge : Theme.of(context).textTheme.labelSmall)
            ?.copyWith(color: color, fontWeight: FontWeight.w700, letterSpacing: 0.4),
      ),
    );
  }
}

class SeverityChip extends StatelessWidget {
  const SeverityChip({super.key, required this.severity});

  final String severity;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final colour = switch (severity.toLowerCase()) {
      'critical' => scheme.error,
      'warning' => Colors.orange.shade700,
      'watch' => Colors.blueGrey.shade600,
      _ => scheme.outline,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: colour.withValues(alpha: 0.5)),
      ),
      child: Text(
        severityLabel(severity),
        style: Theme.of(context)
            .textTheme
            .labelSmall
            ?.copyWith(color: colour, fontWeight: FontWeight.w600),
      ),
    );
  }
}

/// A key/value row, used for location and incident facts.
class FactRow extends StatelessWidget {
  const FactRow({super.key, required this.label, required this.value, this.icon});

  final String label;
  final String value;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 132,
            child: Row(
              children: [
                if (icon != null) ...[
                  Icon(icon, size: 14, color: theme.colorScheme.outline),
                  const SizedBox(width: 6),
                ],
                Flexible(
                  child: Text(
                    label,
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: SelectableText(value, style: theme.textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }
}

/// The evidence behind an incident: the citizen reports its ids point at, plus
/// the published run it is associated with.
///
/// Ids are shown as well as the joined rows, because an id is what the incident
/// actually stores — the report text is a convenience the console looks up.
class EvidenceList extends StatelessWidget {
  const EvidenceList({
    super.key,
    required this.incident,
    required this.reportsById,
    required this.reportsError,
  });

  final Incident incident;

  /// Reports to join against, keyed by id. Built by the caller from the public
  /// reports list; empty when that list could not be loaded.
  final Map<int, CitizenReport> reportsById;

  /// Why the report list is missing, when it is. Shown rather than swallowed,
  /// so a blank card never reads as "this report had no detail".
  final String? reportsError;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final ids = incident.evidenceReportIds;

    if (ids.isEmpty) {
      return Text(
        'No evidence report ids on this incident.',
        style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (reportsError != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Text(
              'Could not load the report text: $reportsError',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
            ),
          ),
        for (final id in ids)
          Card(
            margin: const EdgeInsets.symmetric(vertical: 4),
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text('Report #$id', style: theme.textTheme.labelLarge),
                      const SizedBox(width: 8),
                      if (reportsById[id] != null)
                        Text(
                          sourceKindLabel(reportsById[id]!.kind),
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                        ),
                    ],
                  ),
                  if (reportsById[id] != null) ...[
                    const SizedBox(height: 4),
                    Text(
                      'Smoke ${reportsById[id]!.smokeIntensity}/5 · '
                      '${formatDuration(reportsById[id]!.durationHours)} · '
                      '${relativeTime(reportsById[id]!.reportedAt)}',
                    ),
                    if ((reportsById[id]!.notes ?? '').trim().isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text('“${reportsById[id]!.notes}”',
                            style: theme.textTheme.bodySmall),
                      ),
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        'Resident submission — unverified, not a measurement.',
                        style: theme.textTheme.labelSmall
                            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                      ),
                    ),
                  ] else
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        'Not in the current reports list (it may have expired, or been '
                        'filed against a different backend).',
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                      ),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

String sourceKindLabel(String kind) {
  switch (kind) {
    case 'building_fire':
      return 'Building fire';
    case 'industrial_fire':
      return 'Industrial fire';
    case 'forest_fire':
      return 'Forest fire';
    case 'crop_burning':
      return 'Wood / crop burning';
    case 'other':
      return 'Other burning';
    default:
      return kind;
  }
}

/// A failure the responder can act on. The server's own message is shown
/// verbatim — it explains *why* a change was refused, which a generic string
/// would throw away.
class FailurePanel extends StatelessWidget {
  const FailurePanel({
    super.key,
    required this.title,
    required this.error,
    this.onRetry,
    this.retryLabel = 'Retry',
  });

  final String title;
  final ApiException error;
  final VoidCallback? onRetry;
  final String retryLabel;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isNetwork = error.isNetwork;
    return Card(
      color: theme.colorScheme.errorContainer,
      margin: const EdgeInsets.symmetric(vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(isNetwork ? Icons.wifi_off : Icons.error_outline,
                    size: 18, color: theme.colorScheme.onErrorContainer),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    title,
                    style: theme.textTheme.titleSmall
                        ?.copyWith(color: theme.colorScheme.onErrorContainer),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              error.message,
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onErrorContainer),
            ),
            if (!error.retryable && !error.isNetwork)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Retrying the same change will not help — the state has to allow it first. '
                  'Pull to refresh to pick up the server\'s current state.',
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: theme.colorScheme.onErrorContainer),
                ),
              ),
            if (onRetry != null && error.retryable)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: FilledButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh, size: 16),
                  label: Text(retryLabel),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
