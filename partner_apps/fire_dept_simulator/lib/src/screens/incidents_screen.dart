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
  static const _incidentPageSize = IncidentApi.incidentPageSize;

  List<Incident> _incidents = const [];
  List<Map<String, dynamic>> _hotspotEvents = const [];
  List<FireDetection> _fireDetections = const [];
  String? _firmsError;
  String? _openingHotspotId;
  int _pendingAssignments = 0;
  PublicationMeta? _publicationMeta;
  bool _loading = true;
  bool _loadingMore = false;
  bool _hasLoadedSnapshot = false;
  bool _hasMoreIncidents = true;
  ApiException? _error;
  bool _requestInFlight = false;
  bool _reloadQueued = false;
  int _queryGeneration = 0;
  bool _dispatching = false;
  Timer? _refreshTimer;
  final ScrollController _scrollController = ScrollController();
  final Set<int> _seenDeliveryIds = {};
  bool _hasInboxSnapshot = false;

  /// Only fire-department incidents by default: this console acts as the fire
  /// department, and the API refuses a role that does not own an incident, so
  /// showing pollution-control work as actionable would be misleading.
  bool _roleOnly = true;
  bool _archiveOnly = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
    _refreshTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (!_archiveOnly) _load();
    });
  }

  @override
  void didUpdateWidget(covariant IncidentsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.api != widget.api ||
        oldWidget.config.baseUrl != widget.config.baseUrl ||
        oldWidget.config.apiKey != widget.config.apiKey ||
        oldWidget.config.role != widget.config.role ||
        oldWidget.config.actorId != widget.config.actorId) {
      _seenDeliveryIds.clear();
      _hasInboxSnapshot = false;
      _resetQueueSnapshot();
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
    _scrollController.dispose();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  void _resetQueueSnapshot() {
    _queryGeneration++;
    _incidents = const [];
    _hotspotEvents = const [];
    _fireDetections = const [];
    _firmsError = null;
    _error = null;
    _loading = true;
    _loadingMore = false;
    _hasLoadedSnapshot = false;
    _hasMoreIncidents = true;
    _pendingAssignments = 0;
    _publicationMeta = null;
  }

  void _changeQueueFilter({bool? roleOnly, bool? archiveOnly}) {
    setState(() {
      if (roleOnly != null) _roleOnly = roleOnly;
      if (archiveOnly != null) _archiveOnly = archiveOnly;
      _resetQueueSnapshot();
    });
    if (_scrollController.hasClients) _scrollController.jumpTo(0);
    _load();
  }

  Future<void> _load({bool append = false}) async {
    if (_requestInFlight) {
      _reloadQueued = true;
      return;
    }
    _requestInFlight = true;
    final generation = _queryGeneration;
    final api = widget.api;
    final baseUrl = widget.config.baseUrl;
    final apiKey = widget.config.apiKey;
    final actorId = widget.config.actorId;
    final role = widget.config.role;
    final roleOnly = _roleOnly;
    final archiveOnly = _archiveOnly;
    final pageCount = append || !_hasLoadedSnapshot || _incidents.isEmpty
        ? 1
        : (_incidents.length + _incidentPageSize - 1) ~/ _incidentPageSize;
    final offsets = append
        ? [_incidents.length]
        : List<int>.generate(pageCount, (index) => index * _incidentPageSize);
    setState(() {
      if (!_hasLoadedSnapshot) _loading = true;
      _error = null;
      _loadingMore = append;
    });
    try {
      // The FIRMS candidate feed and publication pointer are independent of the
      // existing incident and hotspot reads; start all reads together.
      final Future<List<Map<String, dynamic>>> hotspotEventsFuture =
          api.listHotspotEvents().catchError((Object _) => <Map<String, dynamic>>[]);
      String? firmsReadError;
      final Future<List<FireDetection>> fireDetectionsFuture =
          api.listFireDetections(sinceHours: 6).catchError((Object error) {
        firmsReadError = error is ApiException ? error.message : error.toString();
        return <FireDetection>[];
      });
      final Future<PublicationMeta?> publicationMetaFuture =
          api.getPublicationMeta().then<PublicationMeta?>((m) => m).catchError((Object _) => null);

      final results = await Future.wait([
        Future.wait(
          offsets.map(
            (offset) => api.listIncidents(
              role: roleOnly ? role : null,
              archive: archiveOnly,
              limit: _incidentPageSize,
              offset: offset,
            ),
          ),
        ),
        api.listInbox(role),
      ]);
      final pages = results[0] as List<List<Incident>>;
      final fetchedIncidents = pages.expand((page) => page).toList(growable: false);
      final inbox = results[1] as List<IncidentInboxItem>;
      final hotspotEvents = await hotspotEventsFuture;
      final fireDetections = await fireDetectionsFuture;
      final publicationMeta = await publicationMetaFuture;
      if (!mounted) return;
      if (!_isCurrentQuery(
        generation: generation,
        api: api,
        baseUrl: baseUrl,
        apiKey: apiKey,
        actorId: actorId,
        role: role,
        roleOnly: roleOnly,
        archiveOnly: archiveOnly,
      )) {
        _reloadQueued = true;
        return;
      }

      final newlyAssigned = _hasInboxSnapshot
          ? inbox
              .where((item) =>
                  item.isOpen && !_seenDeliveryIds.contains(item.delivery.id))
              .toList(growable: false)
          : const <IncidentInboxItem>[];
      _seenDeliveryIds.addAll(inbox.map((item) => item.delivery.id));
      _hasInboxSnapshot = true;

      setState(() {
        _incidents = append
            ? [..._incidents, ...fetchedIncidents]
            : fetchedIncidents;
        _hasMoreIncidents = pages.last.length == _incidentPageSize;
        _hotspotEvents = hotspotEvents;
        _fireDetections = fireDetections;
        _firmsError = firmsReadError;
        _pendingAssignments = inbox.length;
        _publicationMeta = publicationMeta ?? _publicationMeta;
        _loading = false;
        _loadingMore = false;
        _hasLoadedSnapshot = true;
      });
      if (newlyAssigned.isNotEmpty) _showAssignmentAlert(newlyAssigned);
    } catch (error) {
      if (!mounted) return;
      if (!_isCurrentQuery(
        generation: generation,
        api: api,
        baseUrl: baseUrl,
        apiKey: apiKey,
        actorId: actorId,
        role: role,
        roleOnly: roleOnly,
        archiveOnly: archiveOnly,
      )) {
        _reloadQueued = true;
        return;
      }
      setState(() {
        _error = error is ApiException
            ? error
            : ApiException.network(error.toString());
        if (_publicationMeta != null) {
          _publicationMeta = PublicationMeta(
            latestRunId: _publicationMeta!.latestRunId,
            generatedAt: _publicationMeta!.generatedAt,
            dataMode: _publicationMeta!.dataMode,
            isDemo: _publicationMeta!.isDemo,
            isStale: true,
            ageSeconds: DateTime.now().difference(_publicationMeta!.generatedAt).inSeconds.toDouble(),
          );
        }
        _loading = false;
        _loadingMore = false;
      });
    } finally {
      _requestInFlight = false;
      if (_reloadQueued && mounted) {
        _reloadQueued = false;
        unawaited(_load());
      }
    }
  }

  bool _isCurrentQuery({
    required int generation,
    required IncidentApi api,
    required String baseUrl,
    required String apiKey,
    required String actorId,
    required ResponderRole role,
    required bool roleOnly,
    required bool archiveOnly,
  }) =>
      generation == _queryGeneration &&
      identical(widget.api, api) &&
      widget.config.baseUrl == baseUrl &&
      widget.config.apiKey == apiKey &&
      widget.config.actorId == actorId &&
      widget.config.role == role &&
      _roleOnly == roleOnly &&
      _archiveOnly == archiveOnly;

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
    final meta = _publicationMeta ?? await widget.api.getPublicationMeta().catchError((_) => null);
    if (meta != null && meta.isStale) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Cannot dispatch alerts: latest publication data is stale or unreachable. Refresh once backend publication is restored.'),
        ),
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

  Future<void> _openHotspot(Map<String, dynamic> event) async {
    final eventId = event['event_id'] as String?;
    if (eventId == null || eventId.isEmpty) return;
    if (widget.config.role != ResponderRole.pollutionControl) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Set the simulator role to pollution control to review hotspots.')),
      );
      return;
    }
    setState(() => _openingHotspotId = eventId);
    try {
      final outcome = await widget.api.createIncident(
        sourceType: IncidentSourceType.hotspotEvent,
        sourceRef: eventId,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(outcome.created
              ? 'Hotspot opened as pollution-control incident #${outcome.value.id}.'
              : 'Hotspot is already in incident #${outcome.value.id}.'),
        ),
      );
      await _load();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _openingHotspotId = null);
    }
  }

  Future<void> _openFireDetection(FireDetection detection) async {
    if (widget.config.role != ResponderRole.fireDepartment) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Set the simulator role to fire department to review FIRMS candidates.')),
      );
      return;
    }
    setState(() => _openingHotspotId = detection.id);
    try {
      final outcome = await widget.api.createIncident(
        sourceType: IncidentSourceType.firmsDetection,
        sourceRef: detection.id,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(outcome.created
              ? 'Satellite candidate added as incident #${outcome.value.id} for review.'
              : 'Satellite candidate is already in incident #${outcome.value.id}.'),
        ),
      );
      await _load();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _openingHotspotId = null);
    }
  }

  Future<void> _reviewHotspot(Map<String, dynamic> event, String state) async {
    final eventId = event['event_id'] as String?;
    if (eventId == null || eventId.isEmpty) return;
    if (widget.config.role != ResponderRole.pollutionControl) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Set the simulator role to pollution control to review hotspots.')),
      );
      return;
    }
    setState(() => _openingHotspotId = eventId);
    try {
      await widget.api.reviewHotspotEvent(eventId: eventId, reviewState: state);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(state == 'confirmed' ? 'Hotspot confirmed.' : 'Hotspot dismissed.')),
      );
      await _load();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _openingHotspotId = null);
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
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: Image.asset(
                'assets/images/logo.png',
                width: 28,
                height: 28,
                fit: BoxFit.cover,
                errorBuilder: (_, __, ___) => const Icon(Icons.local_fire_department, color: Colors.deepOrange),
              ),
            ),
            const SizedBox(width: 10),
            const Text('Fire Dept Simulator'),
          ],
        ),
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
          if (_publicationMeta != null)
            Container(
              width: double.infinity,
              color: _publicationMeta!.isStale
                  ? Colors.amber.shade900
                  : Theme.of(context).colorScheme.surfaceVariant,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: Row(
                children: [
                  Icon(
                    _publicationMeta!.isStale ? Icons.warning_amber_rounded : Icons.sensors_outlined,
                    size: 16,
                    color: _publicationMeta!.isStale
                        ? Colors.white
                        : Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _publicationMeta!.isStale
                          ? 'Live data stale / offline · Retaining dated snapshot · Auto-dispatch paused'
                          : 'Publication: ${_publicationMeta!.dataMode.toUpperCase()} · Run: ${_publicationMeta!.latestRunId.length > 8 ? _publicationMeta!.latestRunId.substring(0, 8) : _publicationMeta!.latestRunId}${_publicationMeta!.ageSeconds != null ? ' · Age: ${(_publicationMeta!.ageSeconds! / 60).round()}m' : ''}',
                      style: TextStyle(
                        fontSize: 12,
                        color: _publicationMeta!.isStale
                            ? Colors.white
                            : Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                ],
              ),
            ),
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
                    '${_archiveOnly ? 'Archived' : 'Active'} · '
                    '${_roleOnly ? roleLabel(widget.config.role) : 'All authority'} incidents',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Switch(
                  value: _roleOnly,
                  onChanged: (value) => _changeQueueFilter(roleOnly: value),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
            child: Row(
              children: [
                const Expanded(child: Text('Show resolved and cancelled incidents')),
                Switch(
                  value: _archiveOnly,
                  onChanged: (value) => _changeQueueFilter(archiveOnly: value),
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
    if (error != null && !_hasLoadedSnapshot) {
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

    final openHotspots = _hotspotEvents.where((event) {
      final eventId = event['event_id'];
      return event['synthetic'] != true &&
          (event['status'] == 'potential' || event['status'] == 'confirmed') &&
          !_incidents.any((incident) => incident.sourceRef == eventId);
    }).toList(growable: false);
    final openFireDetections = _fireDetections.where((fire) =>
        !_incidents.any((incident) =>
            incident.sourceType == IncidentSourceType.firmsDetection &&
            incident.sourceRef == fire.id)).toList(growable: false);

    if (_incidents.isEmpty && openHotspots.isEmpty && openFireDetections.isEmpty &&
        _firmsError == null && error == null) {
      return RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          controller: _scrollController,
          physics: const AlwaysScrollableScrollPhysics(),
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
        controller: _scrollController,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        itemCount: (error == null ? 0 : 1) +
            (_firmsError == null ? 0 : 1) +
            (openFireDetections.isEmpty ? 0 : openFireDetections.length + 1) +
            (openHotspots.isEmpty ? 0 : openHotspots.length + 1) +
            _incidents.length +
            (_hasMoreIncidents ? 1 : 0),
        itemBuilder: (context, index) {
          var itemIndex = index;
          if (error != null) {
            if (itemIndex == 0) return _refreshErrorCard(error);
            itemIndex--;
          }
          if (_firmsError != null) {
            if (itemIndex == 0) {
              return Card(
                child: ListTile(
                  leading: const Icon(Icons.satellite_alt_outlined),
                  title: const Text('NASA FIRMS candidates unavailable'),
                  subtitle: Text('The feed could not be read; this does not mean there are no detections. $_firmsError'),
                  trailing: IconButton(
                    tooltip: 'Retry FIRMS read',
                    onPressed: _load,
                    icon: const Icon(Icons.refresh),
                  ),
                ),
              );
            }
            itemIndex--;
          }
          if (openFireDetections.isNotEmpty) {
            if (itemIndex == 0) {
              return Padding(
                padding: const EdgeInsets.fromLTRB(4, 8, 4, 4),
                child: Text(
                  'Recent NASA FIRMS detections · review candidates only',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              );
            }
            itemIndex--;
            if (itemIndex < openFireDetections.length) {
              return _fireDetectionCard(openFireDetections[itemIndex]);
            }
            itemIndex -= openFireDetections.length;
          }
          if (openHotspots.isNotEmpty) {
            if (itemIndex == 0) {
              return Padding(
                padding: const EdgeInsets.fromLTRB(4, 8, 4, 4),
                child: Text(
                  'Satellite hotspots awaiting authority review',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              );
            }
            itemIndex--;
            if (itemIndex < openHotspots.length) {
              return _hotspotCard(openHotspots[itemIndex]);
            }
            itemIndex -= openHotspots.length;
          }
          if (itemIndex < _incidents.length) {
            return _incidentCard(_incidents[itemIndex]);
          }
          return _loadMoreCard();
        },
      ),
    );
  }

  Widget _refreshErrorCard(ApiException error) => Card(
        child: ListTile(
          leading: const Icon(Icons.cloud_off_outlined),
          title: const Text('Refresh failed; showing the last loaded queue.'),
          subtitle: Text(error.message),
          trailing: IconButton(
            tooltip: 'Retry refresh',
            onPressed: _load,
            icon: const Icon(Icons.refresh),
          ),
        ),
      );

  Widget _hotspotCard(Map<String, dynamic> hotspot) => Card(
        child: ListTile(
          leading: const Icon(Icons.satellite_alt_outlined),
          title: Text(
            'Potential hotspot · ${hotspot['confidence_band'] ?? 'unrated'} triage',
          ),
          subtitle: Text(
            '${(hotspot['region'] as String?) ?? 'India'} · '
            '${((hotspot['footprint_cells'] as List?) ?? const []).length} cell(s) · '
            '${hotspot['event_id']}',
          ),
          trailing: widget.config.role == ResponderRole.pollutionControl
              ? Wrap(
                  spacing: 4,
                  children: [
                    if (hotspot['status'] == 'potential') ...[
                      TextButton(
                        onPressed: _openingHotspotId == hotspot['event_id']
                            ? null
                            : () => _reviewHotspot(hotspot, 'dismissed'),
                        child: const Text('Dismiss'),
                      ),
                      FilledButton(
                        onPressed: _openingHotspotId == hotspot['event_id']
                            ? null
                            : () => _reviewHotspot(hotspot, 'confirmed'),
                        child: Text(
                          _openingHotspotId == hotspot['event_id']
                              ? 'Saving…'
                              : 'Confirm',
                        ),
                      ),
                    ] else
                      FilledButton(
                        onPressed: _openingHotspotId == hotspot['event_id']
                            ? null
                            : () => _openHotspot(hotspot),
                        child: Text(
                          _openingHotspotId == hotspot['event_id']
                              ? 'Opening…'
                              : 'Open incident',
                        ),
                      ),
                  ],
                )
              : const Icon(Icons.lock_outline),
        ),
      );

  Widget _fireDetectionCard(FireDetection detection) => Card(
        child: ListTile(
          leading: const Icon(Icons.satellite_alt_outlined),
          title: Text(
            '${detection.confidenceClass.toUpperCase()} confidence · '
            'FRP ${detection.frpMw.toStringAsFixed(1)} MW',
          ),
          subtitle: Text(
            '${detection.product} · ${detection.satellite} · '
            '${detection.acquiredAt.toUtc()} UTC · ${detection.id}\n'
            'Satellite-only candidate; no ground confirmation is linked.',
          ),
          isThreeLine: true,
          trailing: widget.config.role == ResponderRole.fireDepartment
              ? FilledButton(
                  onPressed: _openingHotspotId == detection.id
                      ? null
                      : () => _openFireDetection(detection),
                  child: Text(_openingHotspotId == detection.id ? 'Opening…' : 'Review'),
                )
              : const Icon(Icons.lock_outline),
        ),
      );

  Widget _incidentCard(Incident incident) => Card(
        key: ValueKey<int>(incident.id),
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

  Widget _loadMoreCard() => Padding(
        padding: const EdgeInsets.symmetric(vertical: 16),
        child: Center(
          child: TextButton.icon(
            onPressed: _loadingMore ? null : () => _load(append: true),
            icon: _loadingMore
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.expand_more),
            label: Text(_loadingMore ? 'Loading…' : 'Load more incidents'),
          ),
        ),
      );
}
