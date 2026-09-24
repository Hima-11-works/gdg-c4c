/// Incident detail: what happened, where, what evidence there is, and the
/// responder's actions.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../config.dart';
import '../fmt.dart';
import '../models.dart';
import '../widgets.dart';

class IncidentDetailScreen extends StatefulWidget {
  const IncidentDetailScreen({
    super.key,
    required this.api,
    required this.config,
    required this.incidentId,
  });

  final IncidentApi api;
  final SimulatorConfig config;
  final int incidentId;

  @override
  State<IncidentDetailScreen> createState() => _IncidentDetailScreenState();
}

class _IncidentDetailScreenState extends State<IncidentDetailScreen> {
  Incident? _incident;
  List<IncidentEvent> _history = const [];
  Map<int, CitizenReport> _reportsById = const {};
  String? _reportsError;

  bool _loading = true;
  ApiException? _loadError;

  /// Set while a write is in flight. It is the duplicate-tap guard: every
  /// action button is disabled for the duration, so a second tap cannot fire a
  /// second request. (The API is idempotent as a backstop, not as the plan.)
  bool _writeInFlight = false;

  /// The result of the last write, so the console can report what actually
  /// happened instead of assuming the tap worked.
  String? _lastResult;
  ApiException? _writeError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      // Three reads: the incident, its append-only history, and the public
      // report list used to expand the evidence ids. The report list is a
      // convenience — if it fails the incident still renders.
      final incident = await widget.api.getIncident(widget.incidentId);
      final history = await widget.api.history(widget.incidentId);

      Map<int, CitizenReport> reportsById = const {};
      String? reportsError;
      try {
        final reports = await widget.api.listReports();
        reportsById = {for (final report in reports) report.id: report};
      } on ApiException catch (error) {
        reportsError = error.message;
      }

      if (!mounted) return;
      setState(() {
        _incident = incident;
        _history = history;
        _reportsById = reportsById;
        _reportsError = reportsError;
        _loading = false;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _loadError = error;
        _loading = false;
      });
    }
  }

  /// Run one write, then re-read the incident from the server.
  ///
  /// The re-read is not optional: the server is the authority on status, so
  /// after any accepted change (or any refusal) the console adopts what the
  /// server now says rather than trusting its own optimistic view.
  Future<void> _runWrite(
    String label,
    Future<Incident> Function() write, {
    String Function(Incident)? successMessage,
  }) async {
    if (_writeInFlight) return;
    setState(() {
      _writeInFlight = true;
      _writeError = null;
      _lastResult = null;
    });
    try {
      final before = _incident?.status;
      final updated = await write();
      if (!mounted) return;
      setState(() {
        _incident = updated;
        _lastResult = successMessage?.call(updated) ??
            (before == updated.status
                // The server accepts a repeat of the current status as a no-op,
                // which is what makes a duplicated tap harmless — and worth
                // saying out loud rather than claiming a change happened.
                ? 'Simulated response: $label — already ${statusLabel(updated.status).toLowerCase()}, nothing changed.'
                : 'Simulated response: $label — status is now ${statusLabel(updated.status).toLowerCase()}.');
      });
      await _refreshHistory();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _writeError = error);
      // A refusal usually means this console's idea of the status is behind the
      // server's (another responder moved it). Re-read so the buttons match.
      if (error.isConflict || error.isNotFound) {
        await _refreshHistory();
      }
    } finally {
      if (mounted) setState(() => _writeInFlight = false);
    }
  }

  Future<void> _refreshHistory() async {
    try {
      final history = await widget.api.history(widget.incidentId);
      if (!mounted) return;
      setState(() => _history = history);
    } on ApiException {
      // History already loaded once; a failed refresh must not blank it.
    }
  }

  Future<void> _assign() async {
    final incident = _incident;
    if (incident == null) return;
    final controller = TextEditingController(text: incident.assignee ?? '');
    final assignee = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Assign this incident'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Name the unit or person taking it. This is a simulation — no one is '
                'notified.'),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              autofocus: true,
              decoration: const InputDecoration(
                labelText: 'Unit / assignee',
                hintText: 'e.g. unit-12',
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(controller.text.trim()),
            child: const Text('Assign'),
          ),
        ],
      ),
    );
    if (assignee == null || assignee.isEmpty) return;
    // The dialog awaited; the responder may have left the screen while it was
    // open, so nothing below may touch state without checking first.
    if (!mounted) return;
    await _runWrite(
      'Assign to $assignee',
      () => widget.api.assign(
        incident.id,
        role: widget.config.role,
        assignee: assignee,
      ),
      successMessage: (updated) =>
          'Simulated response: assigned to ${updated.assignee} — status is now ${statusLabel(updated.status).toLowerCase()}.',
    );
  }

  Widget _buildActions(Incident incident) {
    if (incident.status == null) {
      return const Text('This incident reports a status this console does not recognise, so it '
          'offers no actions for it.');
    }
    if (incident.status!.isTerminal) {
      return Text(
        'This incident is ${statusLabel(incident.status).toLowerCase()} — a terminal state. '
        'The workflow allows no further changes.',
      );
    }
    if (!incident.isFireDepartment) {
      // The API refuses a role that does not own the incident, so the console
      // does not offer the button at all.
      return Text(
        'This incident belongs to ${roleLabel(incident.responderRole)}, not the fire department. '
        'This console acts only as the fire department, so it offers no actions here.',
      );
    }

    final actions = actionsFor(incident.status!);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final action in actions)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: SizedBox(
              width: double.infinity,
              child: action.isAssign
                  ? OutlinedButton.icon(
                      onPressed: _writeInFlight ? null : _assign,
                      icon: const Icon(Icons.assignment_ind_outlined, size: 18),
                      label: Text(action.label),
                    )
                  : FilledButton.icon(
                      onPressed: _writeInFlight
                          ? null
                          : () => _runWrite(
                                action.label,
                                () => widget.api.transition(
                                  incident.id,
                                  toStatus: action.target!,
                                  role: widget.config.role,
                                ),
                              ),
                      icon: _writeInFlight
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Icon(
                              action.target == IncidentStatus.cancelled
                                  ? Icons.cancel_outlined
                                  : Icons.arrow_forward,
                              size: 18,
                            ),
                      label: Text(action.label),
                    ),
            ),
          ),
        if (_writeInFlight)
          const Padding(
            padding: EdgeInsets.only(top: 4),
            child: Text('Sending one change — the other actions are held until it answers.'),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Incident #${widget.incidentId}'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          const SimulationBanner(dense: true),
          if (!widget.config.hasKey)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.errorContainer,
              padding: const EdgeInsets.all(10),
              child: Text(
                'No simulator key configured — this console can read but not change anything. '
                'Add one in Settings.',
                style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer),
              ),
            ),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    final incident = _incident;
    if (incident == null) {
      return ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FailurePanel(
            title: 'Could not load this incident',
            error: _loadError ?? ApiException.network('Unknown error'),
            onRetry: _load,
          ),
        ],
      );
    }

    final theme = Theme.of(context);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
        children: [
          Row(
            children: [
              StatusChip(status: incident.status, large: true),
              const SizedBox(width: 10),
              SeverityChip(severity: incident.severity),
            ],
          ),
          const SizedBox(height: 12),

          // Location and identity
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Where', style: theme.textTheme.titleSmall),
                  const SizedBox(height: 6),
                  FactRow(
                    label: 'Location',
                    value: formatCoordinates(incident.latitude, incident.longitude),
                    icon: Icons.place_outlined,
                  ),
                  FactRow(
                    label: 'Jurisdiction',
                    value: incident.jurisdiction ?? 'Not recorded',
                    icon: Icons.account_balance_outlined,
                  ),
                  FactRow(
                    label: 'H3 cell',
                    value: incident.h3Cell ?? 'Not recorded',
                    icon: Icons.grid_on_outlined,
                  ),
                  FactRow(
                    label: 'Source',
                    value: '${sourceLabel(incident.sourceType)} #${incident.sourceId}',
                    icon: Icons.link,
                  ),
                  FactRow(
                    label: 'Assigned to',
                    value: incident.assignee ?? 'Unassigned',
                    icon: Icons.person_outline,
                  ),
                  FactRow(
                    label: 'Opened',
                    value: '${relativeTime(incident.createdAt)} '
                        '(${incident.createdAt.toLocal()})',
                    icon: Icons.schedule,
                  ),
                  if (incident.resolvedAt != null)
                    FactRow(
                      label: 'Closed',
                      value: relativeTime(incident.resolvedAt!),
                      icon: Icons.check_circle_outline,
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // Actions
          Text('Responder actions', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          _buildActions(incident),

          if (_lastResult != null)
            Card(
              color: theme.colorScheme.secondaryContainer,
              margin: const EdgeInsets.only(top: 4, bottom: 8),
              child: Padding(
                padding: const EdgeInsets.all(10),
                child: Row(
                  children: [
                    const Icon(Icons.check_circle_outline, size: 18),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(_lastResult!,
                          style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.onSecondaryContainer)),
                    ),
                  ],
                ),
              ),
            ),

          if (_writeError != null)
            FailurePanel(
              title: 'The change was refused',
              error: _writeError!,
              onRetry: null,
            ),

          const SizedBox(height: 8),

          // Evidence
          Text('Evidence', style: theme.textTheme.titleSmall),
          const SizedBox(height: 4),
          Text(
            incident.linkedPredictionRunId == null
                ? 'No published run linked.'
                : 'Linked published run: ${incident.linkedPredictionRunId}',
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: 6),
          EvidenceList(
            incident: incident,
            reportsById: _reportsById,
            reportsError: _reportsError,
          ),

          const SizedBox(height: 16),

          // History
          Text('Event history (append-only)', style: theme.textTheme.titleSmall),
          const SizedBox(height: 6),
          if (_history.isEmpty)
            Text('No events recorded.',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          for (final event in _history)
            Card(
              margin: const EdgeInsets.symmetric(vertical: 3),
              child: ListTile(
                dense: true,
                leading: Icon(_eventIcon(event.eventType), size: 20),
                title: Text(
                  event.fromStatus == event.toStatus || event.toStatus == null
                      ? eventLabel(event.eventType)
                      : '${statusLabel(event.fromStatus)} → ${statusLabel(event.toStatus)}',
                ),
                subtitle: Text(
                  [
                    relativeTime(event.createdAt),
                    if (event.actor != null && event.actor!.isNotEmpty) 'by ${event.actor}',
                    if (event.note != null && event.note!.isNotEmpty) '“${event.note}”',
                  ].join(' · '),
                ),
              ),
            ),
          const SizedBox(height: 16),
          Text(
            'This console writes to the incident API and nothing else. No notification, '
            'dispatch or alert leaves it — the incident record is the only effect.',
            style: theme.textTheme.labelSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }

  IconData _eventIcon(IncidentEventType? type) {
    switch (type) {
      case IncidentEventType.created:
        return Icons.add_circle_outline;
      case IncidentEventType.assigned:
      case IncidentEventType.reassigned:
        return Icons.assignment_ind_outlined;
      case IncidentEventType.transition:
        return Icons.sync_alt;
      case null:
        return Icons.help_outline;
    }
  }
}
