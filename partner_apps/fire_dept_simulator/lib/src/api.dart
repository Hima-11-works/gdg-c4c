/// HTTP client for the incident workflow API (`docs/api/incidents.md`).
///
/// Deliberately built on `dart:io` rather than a package: this app is a
/// simulator console that has to build with nothing but the Flutter SDK, and
/// the surface it needs — one origin, JSON in, JSON out, a header for writes —
/// is small enough that a package would be more moving parts than it removes.
/// (The trade-off: `dart:io` does not exist on Flutter web, so this console is
/// a mobile/desktop tool.)
///
/// Two things this client refuses to do:
///   - invent a status code. A transport failure is [ApiException.isNetwork],
///     never a fabricated HTTP status, so callers can tell "the server said no"
///     from "I never reached the server."
///   - hide the server's message. The backend explains *why* a transition was
///     refused; that text is surfaced to the responder instead of being
///     replaced with a generic failure.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'models.dart';

/// A failed call: either the server answered with an error, or it could not be
/// reached.
class ApiException implements Exception {
  ApiException({
    required this.status,
    required this.code,
    required this.message,
    this.isNetwork = false,
  });

  /// Transport failure: no HTTP status exists.
  factory ApiException.network(String message) =>
      ApiException(status: 0, code: 'network_error', message: message, isNetwork: true);

  /// The app refused to send a write because no simulator key is configured.
  /// Distinct from the server's 503: nothing was sent at all.
  factory ApiException.missingKey() => ApiException(
        status: 0,
        code: 'missing_credentials',
        message: 'A simulator key and registered actor id are required before this console can change anything. '
            'Set both in Settings — writes are refused without them.',
      );

  final int status;
  final String code;
  final String message;

  /// True when the request never got an answer (DNS, refused connection,
  /// timeout, socket dropped). The safe response is to offer a retry.
  final bool isNetwork;

  bool get isUnauthorized => status == 401;
  bool get isRoleMismatch => status == 403;
  bool get isConflict => status == 409;
  bool get isNotFound => status == 404;
  bool get isSimulatorDisabled => status == 503;

  /// Whether trying the same call again could plausibly work. A refused
  /// transition cannot, so it is not offered a retry; a dropped connection can.
  bool get retryable => isNetwork || status >= 500;

  @override
  String toString() => isNetwork ? message : '$message (HTTP $status · $code)';
}

/// Outcome of a write, so the UI can say what actually happened rather than
/// assuming success.
class WriteOutcome<T> {
  const WriteOutcome({required this.value, required this.created});

  final T value;

  /// False when the server treated the write as an idempotent repeat (the
  /// create returned an existing incident, or a transition was already applied).
  final bool created;
}

/// A successful response, kept with its status code because the codes carry
/// meaning here: create is `201` for a new incident and `200` for an idempotent
/// repeat, and the console says which one happened.
class _ApiResponse {
  const _ApiResponse(this.status, this.data);

  final int status;
  final dynamic data;
}

class IncidentApi {
  IncidentApi({
    required String baseUrl,
    String? apiKey,
    this.actorId = '',
    this.timeout = const Duration(seconds: 12),
  })  : baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), ''),
        apiKey = (apiKey == null || apiKey.isEmpty) ? null : apiKey;

  final String baseUrl;
  final String? apiKey;
  final String actorId;
  final Duration timeout;

  final HttpClient _client = HttpClient();

  bool get canWrite => apiKey != null && actorId.trim().isNotEmpty;

  void close() => _client.close(force: true);

  Uri _uri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl$path');
    return query == null || query.isEmpty ? uri : uri.replace(queryParameters: query);
  }

  Future<_ApiResponse> _send(
    String method,
    String path, {
    Object? body,
    bool needsKey = false,
    Map<String, String>? query,
  }) async {
    if (needsKey && !canWrite) throw ApiException.missingKey();

    HttpClientResponse response;
    String text;
    try {
      final request = await _client.openUrl(method, _uri(path, query)).timeout(timeout);
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      if (needsKey) {
        request.headers.set('X-Simulator-Key', apiKey!);
        request.headers.set('X-Actor-Id', actorId.trim());
      }
      if (body != null) {
        request.headers.contentType = ContentType('application', 'json', charset: 'utf-8');
        request.add(utf8.encode(jsonEncode(body)));
      }
      response = await request.close().timeout(timeout);
      text = await response.transform(utf8.decoder).join().timeout(timeout);
    } on TimeoutException {
      throw ApiException.network('The backend did not answer in time. Check it is running, then retry.');
    } on SocketException catch (error) {
      throw ApiException.network('Could not reach the backend at $baseUrl (${error.osError?.message ?? 'connection failed'}).');
    } on HttpException catch (error) {
      throw ApiException.network('The connection failed: ${error.message}');
    } on HandshakeException catch (error) {
      throw ApiException.network('TLS handshake failed: ${error.message}');
    }

    dynamic decoded;
    if (text.trim().isNotEmpty) {
      try {
        decoded = jsonDecode(text);
      } on FormatException {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          throw ApiException(
            status: response.statusCode,
            code: 'bad_response',
            message: 'The backend returned a body this console could not read.',
          );
        }
        decoded = null;
      }
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      // The platform's error shape: {"error": {"code", "message", "details"?}}.
      // When it is missing, the status alone is reported rather than invented.
      final error = (decoded is Map && decoded['error'] is Map) ? decoded['error'] as Map : null;
      throw ApiException(
        status: response.statusCode,
        code: (error?['code'] as String?) ?? 'http_error',
        message: (error?['message'] as String?) ??
            'The backend refused this request (HTTP ${response.statusCode}).',
      );
    }

    if (decoded is! Map || !decoded.containsKey('data')) {
      throw ApiException(
        status: response.statusCode,
        code: 'bad_response',
        message: 'The backend response was missing its data envelope.',
      );
    }
    return _ApiResponse(response.statusCode, decoded['data']);
  }

  // --- reads (public: no key required) ---

  Future<List<Incident>> listIncidents({IncidentStatus? status, ResponderRole? role}) async {
    final query = <String, String>{};
    if (status != null) query['status'] = wireOfStatus(status);
    if (role != null) query['role'] = wireOfRole(role);
    final response = await _send('GET', '/api/v1/incidents', query: query);
    return (response.data as List)
        .map((row) => Incident.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Pending assignments for this console's authority role. These are
  /// persistent simulated inbox entries; the backend sends no push message.
  Future<List<IncidentInboxItem>> listInbox(ResponderRole role) async {
    final response = await _send(
      'GET',
      '/api/v1/incidents/inbox',
      query: {'role': wireOfRole(role), 'only_open': 'true'},
    );
    return (response.data as List)
        .map((row) => IncidentInboxItem.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<Incident> getIncident(int id) async {
    final response = await _send('GET', '/api/v1/incidents/$id');
    return Incident.fromJson(response.data as Map<String, dynamic>);
  }

  Future<List<IncidentEvent>> history(int id) async {
    final response = await _send('GET', '/api/v1/incidents/$id/history');
    return (response.data as List)
        .map((row) => IncidentEvent.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Citizen reports, used to expand an incident's `evidence_report_ids` into
  /// something readable. Public endpoint.
  Future<List<CitizenReport>> listReports() async {
    final response = await _send('GET', '/api/v1/reports');
    return (response.data as List)
        .map((row) => CitizenReport.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Current run's PM2.5 alerts, which the operator can select to open a
  /// persistent response incident. Alert ids are stable across retries.
  Future<List<PublishedAlert>> listPublishedAlerts() async {
    final response = await _send('GET', '/api/v2/alerts');
    final alerts = (response.data as List)
        .map((row) => PublishedAlert.fromJson(row as Map<String, dynamic>))
        .toList();
    alerts.sort((a, b) {
      final severity = _severityRank(b.severity).compareTo(_severityRank(a.severity));
      if (severity != 0) return severity;
      return a.forecastHours.compareTo(b.forecastHours);
    });
    return alerts.take(50).toList();
  }

  /// Live candidate events that a pollution-control responder can open for review.
  Future<List<Map<String, dynamic>>> listHotspotEvents() async {
    final response = await _send('GET', '/api/v1/hotspots/events');
    final dynamic data = response.data;
    final List rawList;
    if (data is List) {
      rawList = data;
    } else if (data is Map && data['events'] is List) {
      rawList = data['events'] as List;
    } else {
      rawList = const [];
    }
    return rawList
        .whereType<Map>()
        .map((row) => Map<String, dynamic>.from(row))
        .toList();
  }

  Future<void> reviewHotspotEvent({required String eventId, required String reviewState}) async {
    await _send(
      'POST',
      '/api/v1/hotspots/events/${Uri.encodeComponent(eventId)}/review',
      needsKey: true,
      body: {'review_state': reviewState},
    );
  }

  int _severityRank(String value) => switch (value) {
        'critical' => 3,
        'warning' => 2,
        _ => 1,
  };

  // --- writes (simulator key required) ---

  /// Create an incident from a source. Idempotent on `(source_type, source_id)`:
  /// a repeat returns the existing incident with `created == false` rather than
  /// creating a second one, which is what makes it safe to retry.
  Future<WriteOutcome<Incident>> createIncident({
    required IncidentSourceType sourceType,
    int? sourceId,
    String? sourceRef,
    String? severity,
    String? jurisdiction,
    String? linkedPredictionRunId,
    List<int>? evidenceReportIds,
    double? latitude,
    double? longitude,
  }) async {
    final response = await _send(
      'POST',
      '/api/v1/incidents',
      needsKey: true,
      body: {
        'source_type': wireOfSource(sourceType),
        if (sourceId != null) 'source_id': sourceId,
        if (sourceRef != null) 'source_ref': sourceRef,
        if (severity != null) 'severity': severity,
        if (jurisdiction != null) 'jurisdiction': jurisdiction,
        if (linkedPredictionRunId != null) 'linked_prediction_run_id': linkedPredictionRunId,
        if (evidenceReportIds != null) 'evidence_report_ids': evidenceReportIds,
        if (latitude != null) 'latitude': latitude,
        if (longitude != null) 'longitude': longitude,
      },
    );
    return WriteOutcome(
      value: Incident.fromJson(response.data as Map<String, dynamic>),
      // The documented distinction: 201 is a new incident, 200 is the existing
      // one returned by an idempotent repeat.
      created: response.status == 201,
    );
  }

  /// `reported -> assigned`. Re-assigning an incident updates the assignee and
  /// appends a `reassigned` event rather than editing history.
  Future<Incident> assign(int id, {required ResponderRole role, required String assignee}) async {
    final response = await _send(
      'POST',
      '/api/v1/incidents/$id/assign',
      needsKey: true,
      body: {'role': wireOfRole(role), 'assignee': assignee},
    );
    return Incident.fromJson(response.data as Map<String, dynamic>);
  }

  /// Advance the status. A repeat of the status the incident is already in is
  /// accepted by the server as a no-op, so a duplicated tap is safe; anything
  /// the state machine does not allow comes back as a 409.
  Future<Incident> transition(
    int id, {
    required IncidentStatus toStatus,
    required ResponderRole role,
    String? note,
  }) async {
    final response = await _send(
      'POST',
      '/api/v1/incidents/$id/transitions',
      needsKey: true,
      body: {
        'to_status': wireOfStatus(toStatus),
        'role': wireOfRole(role),
        if (note != null && note.isNotEmpty) 'note': note,
      },
    );
    return Incident.fromJson(response.data as Map<String, dynamic>);
  }
}
