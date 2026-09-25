/// Fire-report transport: DTOs and the API client for /api/v1/reports and its
/// evidence sub-resource.
///
/// Mirrors lib/data/grid/grid_api.dart's split: an abstract
/// [FireReportApiClient] (faked in tests) over a thin
/// [DioFireReportApiClient] that only maps JSON - no report logic lives
/// here. Envelopes are the same `{generated_at, is_demo, data}` shape.
library;

import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../../domain/models/fire_report.dart';
import '../../domain/models/report_evidence.dart';

/// `{generated_at, is_demo, data}` — every grid API response.
class ReportEnvelope<T> {
  const ReportEnvelope({
    required this.generatedAt,
    required this.isDemo,
    required this.data,
  });

  final DateTime generatedAt;
  final bool isDemo;
  final T data;
}

/// One stored fire report, exactly as the backend returns it.
class FireReportDto {
  const FireReportDto({
    required this.id,
    required this.h3Cell,
    required this.latitude,
    required this.longitude,
    required this.kind,
    required this.smokeIntensity,
    required this.durationHours,
    required this.reportedAt,
    this.notes,
    this.clientReportId,
  });

  final int id;
  final String h3Cell;
  final double latitude;
  final double longitude;
  final String kind;
  final int smokeIntensity;
  final double durationHours;
  final String reportedAt;
  final String? notes;
  final String? clientReportId;

  factory FireReportDto.fromJson(Map<String, dynamic> json) => FireReportDto(
        id: (json['id'] as num).toInt(),
        h3Cell: json['h3_cell'] as String,
        latitude: (json['latitude'] as num).toDouble(),
        longitude: (json['longitude'] as num).toDouble(),
        kind: json['kind'] as String,
        smokeIntensity: (json['smoke_intensity'] as num).toInt(),
        durationHours: (json['duration_hours'] as num).toDouble(),
        reportedAt: json['reported_at'] as String,
        notes: json['notes'] as String?,
        clientReportId: json['client_report_id'] as String?,
      );

  FireReport toDomain() => FireReport(
        id: id,
        h3Cell: h3Cell,
        latitude: latitude,
        longitude: longitude,
        kind: FireKind.fromValue(kind),
        smokeIntensity: smokeIntensity,
        durationHours: durationHours,
        reportedAt: DateTime.parse(reportedAt),
        notes: notes,
        clientReportId: clientReportId,
      );
}

/// A photo the resident selected, held in memory until the upload finishes.
///
/// The bytes are read once at pick time so the upload does not depend on the
/// source URI still being readable, and so an interrupted upload can be
/// retried from exactly the same bytes.
class EvidencePhoto {
  const EvidencePhoto({
    required this.name,
    required this.contentType,
    required this.bytes,
  });

  final String name;
  final String contentType;
  final Uint8List bytes;

  int get byteSize => bytes.length;

  /// Mirrors CITIZEN_MEDIA_MAX_BYTES' default. Advisory: the server is what
  /// enforces the cap, and a deployment may configure a different one.
  static const int defaultMaxBytes = 5 * 1024 * 1024;

  /// Mirrors CITIZEN_MEDIA_ALLOWED_TYPES' default. A generic declared type is
  /// accepted because the backend normalises it rather than punishing it.
  static const List<String> defaultAllowedTypes = [
    'image/jpeg',
    'image/png',
    'image/webp',
  ];

  bool get typeIsAcceptable {
    final type = contentType.toLowerCase();
    if (type.isEmpty ||
        type == 'application/octet-stream' ||
        type == 'binary/octet-stream' ||
        type == '*/*') {
      return true;
    }
    return defaultAllowedTypes.contains(type);
  }

  bool get isWithinSizeCap => byteSize <= defaultMaxBytes;
}

/// A failure of the evidence step, carrying enough for the UI to decide whether
/// another attempt could possibly help.
///
/// A rejected photo or a bad reading fails identically next time, so the UI
/// says what to change rather than offering a retry that cannot work; only
/// transport and storage failures are treated as retryable.
class EvidenceUploadException implements Exception {
  const EvidenceUploadException({
    required this.code,
    required this.message,
    this.statusCode,
  });

  /// The backend's own machine code, or `network_error` for a transport
  /// failure - which is not an HTTP status and must never be presented as one.
  final String code;
  final String message;
  final int? statusCode;

  bool get isRetryable {
    if (code == 'conflict') return false;
    final status = statusCode;
    if (status == null) return true;
    if (code == 'media_unavailable' || code == 'media_not_durable') return true;
    return status >= 500;
  }

  /// The human reading of a rejection, kept next to the codes so the UI does
  /// not invent its own wording.
  String get explanation {
    switch (code) {
      case 'network_error':
        return 'The upload never reached the server, or the connection dropped '
            'mid-transfer. Nothing is known about what arrived, so the same '
            'upload can simply be sent again.';
      case 'unsupported_media_type':
        return 'That file type is not accepted. Use a JPEG, PNG or WebP photo.';
      case 'unrecognized_media_content':
        return 'Those bytes are not a photo. A renamed file is not an image.';
      case 'media_content_invalid':
        return 'That photo arrived incomplete - the usual sign of an interrupted '
            'transfer. Pick the photo again and retry.';
      case 'media_content_mismatch':
        return 'The photo does not match the format it was announced as.';
      case 'media_too_large':
        return 'That photo is larger than the server accepts.';
      case 'media_unavailable':
      case 'media_not_durable':
        return 'The server could not store the photo right now. Your report is '
            'safe - retry the upload.';
      case 'validation_error':
        return 'The backend rejected a field. Check the reading and its time.';
      case 'conflict':
        return 'This report already has different evidence stored under the same '
            'key. The stored record was left untouched.';
      case 'not_found':
        return 'The server does not know this report, so the evidence has '
            'nowhere to go.';
      default:
        final status = statusCode;
        return status != null && status >= 500
            ? 'The server could not store the evidence. The report itself is '
                'already safe - retry.'
            : 'The backend refused the evidence.';
    }
  }

  @override
  String toString() => 'EvidenceUploadException($code, status: $statusCode)';
}

/// The fire-report API surface, plus the evidence sub-resource. Implemented by
/// [DioFireReportApiClient] and by in-memory fakes in tests, exactly like the
/// grid client.
abstract class FireReportApiClient {
  Future<FireReport> submitReport(FireReportDraft draft);

  Future<List<FireReport>> listActiveReports();

  /// Attach evidence to a report that already exists.
  ///
  /// [clientReportId] is the evidence idempotency key and must be the same
  /// value on every retry of the same upload: an identical payload returns the
  /// stored record, a different payload under the same key is a conflict. That
  /// is what makes an interrupted upload retryable without producing a second
  /// record.
  ///
  /// [onProgress] receives bytes sent and, when the transport knows it, the
  /// total. A total of 0 means the size is not known yet, not that nothing is
  /// being sent.
  Future<ReportEvidence> submitEvidence({
    required int reportId,
    required String clientReportId,
    required EvidencePhoto? photo,
    required CitizenSensorEvidence? sensor,
    String? notes,
    void Function(int sent, int total)? onProgress,
  });

  /// Read an evidence record back. Throws [EvidenceUploadException] with code
  /// `not_found` when the report has no evidence yet.
  Future<ReportEvidence> fetchEvidence(int reportId);
}

/// Dio-backed [FireReportApiClient]. [dio]'s base URL is the API origin
/// (the same instance base the grid client uses); this class adds the
/// `/api/v1` prefix.
class DioFireReportApiClient implements FireReportApiClient {
  DioFireReportApiClient({required Dio dio}) : _dio = dio;

  final Dio _dio;

  static const _apiPrefix = '/api/v1';

  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    final json = await _post('$_apiPrefix/reports', {
      'latitude': draft.latitude,
      'longitude': draft.longitude,
      'kind': draft.kind.value,
      'smoke_intensity': draft.smokeIntensity,
      'duration_hours': draft.durationHours,
      if (draft.notes != null) 'notes': draft.notes,
      if (draft.clientReportId != null) 'client_report_id': draft.clientReportId,
    });
    return _unwrapEnvelope(
      json,
      (data) => FireReportDto.fromJson(data as Map<String, dynamic>).toDomain(),
    );
  }

  @override
  Future<List<FireReport>> listActiveReports() async {
    final json = await _get('$_apiPrefix/reports');
    return _unwrapEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => FireReportDto.fromJson(e as Map<String, dynamic>).toDomain())
          .toList(),
    );
  }

  @override
  Future<ReportEvidence> submitEvidence({
    required int reportId,
    required String clientReportId,
    required EvidencePhoto? photo,
    required CitizenSensorEvidence? sensor,
    String? notes,
    void Function(int sent, int total)? onProgress,
  }) async {
    if (photo == null && sensor == null) {
      throw const EvidenceUploadException(
        code: 'validation_error',
        message: 'attach a photo, a sensor reading, or both',
      );
    }

    final form = FormData();
    if (photo != null) {
      form.files.add(
        MapEntry(
          'photo',
          MultipartFile.fromBytes(
            photo.bytes,
            filename: photo.name,
            contentType: _mediaTypeFor(photo.contentType),
          ),
        ),
      );
    }
    form.fields.add(MapEntry('client_report_id', clientReportId));
    if (sensor != null) {
      sensor.toFormFields().forEach((key, value) {
        form.fields.add(MapEntry(key, value));
      });
    }
    final trimmedNotes = notes?.trim();
    if (trimmedNotes != null && trimmedNotes.isNotEmpty) {
      form.fields.add(MapEntry('notes', trimmedNotes));
    }

    try {
      final response = await _dio.post<Map<String, dynamic>>(
        '$_apiPrefix/reports/$reportId/evidence',
        data: form,
        onSendProgress: onProgress,
      );
      return _unwrapEnvelope(
        response.data!,
        (data) => ReportEvidence.fromJson(data as Map<String, dynamic>),
      );
    } on DioException catch (error) {
      throw _asEvidenceError(error);
    }
  }

  @override
  Future<ReportEvidence> fetchEvidence(int reportId) async {
    try {
      final json = await _get('$_apiPrefix/reports/$reportId/evidence');
      return _unwrapEnvelope(
        json,
        (data) => ReportEvidence.fromJson(data as Map<String, dynamic>),
      );
    } on DioException catch (error) {
      throw _asEvidenceError(error);
    }
  }

  /// A generic declared type is passed through as octet-stream, which the
  /// backend normalises to the format it actually sniffs. An unparseable type
  /// is treated the same way rather than sent as a guess.
  static DioMediaType _mediaTypeFor(String contentType) {
    final type = contentType.trim().toLowerCase();
    if (type.isEmpty || type == '*/*') {
      return DioMediaType('application', 'octet-stream');
    }
    try {
      return DioMediaType.parse(type);
    } catch (_) {
      return DioMediaType('application', 'octet-stream');
    }
  }

  /// Turn a transport failure into an [EvidenceUploadException] without ever
  /// inventing an HTTP status for a request that never got an answer.
  static EvidenceUploadException _asEvidenceError(DioException error) {
    final status = error.response?.statusCode;
    final body = error.response?.data;
    String? code;
    String? message;
    if (body is Map<String, dynamic>) {
      final detail = body['error'];
      if (detail is Map<String, dynamic>) {
        code = detail['code'] as String?;
        message = detail['message'] as String?;
      }
    }
    if (code == null) {
      return EvidenceUploadException(
        code: 'network_error',
        message: 'The upload did not complete: ${error.message ?? error.type.name}',
      );
    }
    return EvidenceUploadException(
      code: code,
      message: message ?? 'The backend refused the evidence.',
      statusCode: status,
    );
  }

  Future<Map<String, dynamic>> _get(String path) async {
    final response = await _dio.get<Map<String, dynamic>>(path);
    return response.data!;
  }

  Future<Map<String, dynamic>> _post(String path, Map<String, dynamic> body) async {
    final response = await _dio.post<Map<String, dynamic>>(path, data: body);
    return response.data!;
  }

  static ReportEnvelope<T> _parseEnvelope<T>(
    Map<String, dynamic> json,
    T Function(dynamic data) parseData,
  ) {
    return ReportEnvelope<T>(
      generatedAt: DateTime.parse(json['generated_at'] as String),
      isDemo: json['is_demo'] as bool? ?? false,
      data: parseData(json['data']),
    );
  }

  /// Unwrap the platform envelope (`{generated_at, is_demo, data}`) and return
  /// the payload the interface promises.
  ///
  /// The backend wraps every citizen-intake response in an envelope, so the
  /// declared return types are the *inner* record. Returning the envelope here
  /// is a type error, and at runtime it would hand every caller an
  /// `Envelope<FireReport>` where a `FireReport` was expected - a report with
  /// no `id`, so evidence could never be attached to it.
  static T _unwrapEnvelope<T>(
    Map<String, dynamic> json,
    T Function(dynamic data) parseData,
  ) {
    return _parseEnvelope(json, parseData).data;
  }
}
