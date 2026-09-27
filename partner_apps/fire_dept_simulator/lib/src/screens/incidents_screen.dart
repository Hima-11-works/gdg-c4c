/// The queue: incidents this console is responsible for.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../config.dart';
import '../fmt.dart';
import '../models.dart';
import '../widgets.dart';
import 'incident_detail_screen.dart';

class IncidentsScreen extends StatefulWidget {
  const IncidentsScreen({
    super.key,
    required this.api,
    required this.config,
    required this.onOpenSettings,
  });

  final IncidentApi api;
  final SimulatorConfig config;
  final VoidCallback onOpenSettings;

  @override
  State<IncidentsScreen> createState() => _IncidentsScreenState();
}

class _IncidentsScreenState extends State<IncidentsScreen>
    with WidgetsBindingObserver {
  List<Incident> _incidents = const [];
  int _pendingAssignments = 0;
  bool _loading = true;
  ApiException? _error;
  bool _requestInFlight = false;
  bool _dispatching = false;
  Timer? _refreshTimer;
  final Set<int> _seenDeliveryIds = {};
  bool _hasInboxSnapshot = false;

  /// Only fire-department incidents by default: this console acts as the fire
  /// department, and the API refuses a role that does not own an incident, so
  /// showing pollution-control work as actionable would be misleading.
  bool _roleOnly = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
    _refreshTimer = Timer.periodic(const Duration(seconds: 30), (_) => _load());
  }

  @override
  void didUpdateWidget(covariant IncidentsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.api != widget.api ||
        oldWidget.config.role != widget.config.role ||
        oldWidget.config.actorId != widget.config.actorId) {
      _seenDeliveryIds.clear();
      _hasInboxSnapshot = false;
      _load();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  Future<void> _load() async {
    if (_requestInFlight) return;
    _requestInFlight = true;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        widget.api.listIncidents(role: _roleOnly ? widget.config.role : null),
        widget.api.listInbox(widget.config.role),
      ]);
      final incidents = results[0] as List<Incident>;
      final inbox = results[1] as List<IncidentInboxItem>;
      if (!mounted) return;

      final newlyAssigned = _hasInboxSnapshot
          ? inbox
              .where((item) =>
                  item.isOpen && !_seenDeliveryIds.contains(item.delivery.id))
              .toList(growable: false)
          : const <IncidentInboxItem>[];
      _seenDeliveryIds.addAll(inbox.map((item) => item.delivery.id));
      _hasInboxSnapshot = true;

      setState(() {
        _incidents = incidents;
        _pendingAssignments = inbox.length;
        _loading = false;
      });
      if (newlyAssigned.isNotEmpty) _showAssignmentAlert(newlyAssigned);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    } finally {
      _requestInFlight = false;
    }
  }

  void _showAssignmentAlert(List<IncidentInboxItem> items) {
    if (!mounted) return;
    final latest = items.first;
    final message = items.length == 1
        ? 'New ${latest.incident.severity.toUpperCase()} assignment · '
            'incident #${latest.incident.id}'
        : '${items.length} new authority assignments received';
    final messenger = ScaffoldMessenger.of(context);
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          duration: const Duration(seconds: 8),
          action: SnackBarAction(
            label: 'Open',
            onPressed: () => _open(latest.incident),
          ),
        ),
      );
  }

  Future<void> _dispatchAlert() async {
    if (widget.config.role != ResponderRole.pollutionControl) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Set the simulator role to pollution control to open PM2.5 alerts.')),
      );
      return;
    }
    setState(() => _dispatching = true);
    try {
      final alerts = await widget.api.listPublishedAlerts();
      if (!mounted) return;
      if (alerts.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('No published PM2.5 alerts are available in the latest run.')),
        );
        return;
      }
      final selected = await showDialog<PublishedAlert>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Open a PM2.5 response incident'),
          content: SizedBox(
            width: 520,
            height: 420,
            child: ListView.builder(
              itemCount: alerts.length,
              itemBuilder: (context, index) {
                final alert = alerts[index];
                return ListTile(
                  leading: Icon(
                    Icons.air,
                    color: alert.severity == 'critical' ? Colors.red : Colors.deepOrange,
                  ),
                  title: Text('${alert.severity.toUpperCase()} · +${alert.forecastHours}h'),
                  subtitle: Text('${alert.message}  Cell ${alert.h3Cell}'),
                  onTap: () => Navigator.of(context).pop(alert),
                );
              },
            ),
          ),
          actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel'))],
        ),
      );
      if (selected == null || !mounted) return;
      final outcome = await widget.api.createIncident(
        sourceType: IncidentSourceType.publishedAlert,
        sourceRef: selected.alertId,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(outcome.created
              ? 'Incident #${outcome.value.id} added to the authority queue.'
              : 'Incident #${outcome.value.id} is already in the authority queue.'),
        ),
      );
      await _load();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _dispatching = false);
    }
  }

  Future<void> _open(Incident incident) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => IncidentDetailScreen(
          api: widget.api,
          config: widget.config,
          incidentId: incident.id,
        ),
      ),
    );
    // Coming back from a detail screen, the queue may have moved on — another
    // responder can have changed a status while this screen was in the stack.
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Fire Dept Simulator'),
        actions: [
          IconButton(
            tooltip: 'Review published PM2.5 alerts',
            onPressed: _dispatching || _loading ? null : _dispatchAlert,
            icon: _dispatching
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.add_alert_outlined),
          ),
          IconButton(
            tooltip: 'Settings (API address and simulator key)',
            onPressed: widget.onOpenSettings,
            icon: const Icon(Icons.settings_outlined),
          ),
          IconButton(
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          const SimulationBanner(),
          if (!widget.api.canWrite)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.errorContainer,
              padding: const EdgeInsets.all(10),
              child: Text(
                'A simulator key and registered actor id are required for actions. '
                'Reads work; set both in Settings.',
                style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    _roleOnly ? '${roleLabel(widget.config.role)} incidents' : 'All authority incidents',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Switch(
                  value: _roleOnly,
                  onChanged: (value) {
                    setState(() => _roleOnly = value);
                    _load();
                  },
                ),
              ],
            ),
          ),
          if (_pendingAssignments > 0)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.secondaryContainer,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Text(
                '$_pendingAssignments simulated assignment${_pendingAssignments == 1 ? '' : 's'} '
                'awaiting acknowledgement. New assignments alert in this app while it is open; '
                'the inbox refreshes again when you return.',
                style: TextStyle(color: Theme.of(context).colorScheme.onSecondaryContainer),
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

    final error = _error;
    if (error != null) {
      return ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FailurePanel(
            title: error.isNetwork ? 'Cannot reach the backend' : 'The backend refused the read',
            error: error,
            onRetry: _load,
          ),
        ],
      );
    }

    if (_incidents.isEmpty) {
      return RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const SizedBox(height: 40),
            Icon(Icons.inbox_outlined, size: 40, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: 12),
            Text(
              'No incidents to show.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 6),
            Text(
              'Review published PM2.5 alerts with the alert button above. Creating an incident is '
              'a simulated handoff; this console sends no push notification or emergency call.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        itemCount: _incidents.length,
        itemBuilder: (context, index) {
          final incident = _incidents[index];
          return Card(
            child: ListTile(
              onTap: () => _open(incident),
              title: Row(
                children: [
                  Text('#${incident.id}', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(width: 10),
                  StatusChip(status: incident.status),
                ],
              ),
              subtitle: Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        SeverityChip(severity: incident.severity),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            incident.jurisdiction ?? 'No jurisdiction recorded',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(formatCoordinates(incident.latitude, incident.longitude)),
                    Text(
                      '${incident.assignee == null ? 'Unassigned' : 'Assigned to ${incident.assignee}'}'
                      ' · updated ${relativeTime(incident.updatedAt)}'
                      '${incident.isFireDepartment ? '' : ' · ${roleLabel(incident.responderRole)}'}',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Theme.of(context).colorScheme.onSurfaceVariant,
                          ),
                    ),
                  ],
                ),
              ),
              trailing: const Icon(Icons.chevron_right),
              isThreeLine: true,
            ),
          );
        },
      ),
    );
  }
}
