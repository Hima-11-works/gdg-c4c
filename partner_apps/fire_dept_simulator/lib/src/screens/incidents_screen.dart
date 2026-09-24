/// The queue: incidents this console is responsible for.
library;

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

class _IncidentsScreenState extends State<IncidentsScreen> {
  List<Incident> _incidents = const [];
  bool _loading = true;
  ApiException? _error;

  /// Only fire-department incidents by default: this console acts as the fire
  /// department, and the API refuses a role that does not own an incident, so
  /// showing pollution-control work as actionable would be misleading.
  bool _fireOnly = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final incidents = await widget.api.listIncidents(
        role: _fireOnly ? widget.config.role : null,
      );
      if (!mounted) return;
      setState(() {
        _incidents = incidents;
        _loading = false;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
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
          if (!widget.config.hasKey)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.errorContainer,
              padding: const EdgeInsets.all(10),
              child: Text(
                'No simulator key configured. Reads work; every action will be refused. '
                'Set a key in Settings.',
                style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    _fireOnly ? 'Fire-department incidents' : 'All incidents',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Switch(
                  value: _fireOnly,
                  onChanged: (value) {
                    setState(() => _fireOnly = value);
                    _load();
                  },
                ),
              ],
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
              'Incidents are created explicitly from an eligible fire alert or citizen report — '
              'nothing is created here, and there is no automatic creation on ingest. Create one '
              'with POST /api/v1/incidents (see docs/api/incidents.md), then pull to refresh.',
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
