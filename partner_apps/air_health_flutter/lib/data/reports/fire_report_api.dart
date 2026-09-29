/// Fire-report transport: DTOs and the API client for /api/v1/reports.
///
/// Mirrors lib/data/grid/grid_api.dart's split: an abstract
/// [FireReportApiClient] (faked in tests) over a thin
/// [DioFireReportApiClient] that only maps JSON - no report logic lives
/// here. Envelopes are the same `{generated_at, is_demo, data}` shape.
library;

import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../../domain/models/fire_report.dart';

/// Optional photo upload surface for clients that support citizen evidence.
abstract interface class CitizenPhotoApiClient {
  Future<void> attachPhoto({
    required int reportId,
    required Uint8List bytes,
    required String filename,
  });
}

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

/// The fire-report API surface. Implemented by [DioFireReportApiClient] and
/// by in-memory fakes in tests, exactly like the grid client.
abstract class FireReportApiClient {
  Future<FireReport> submitReport(FireReportDraft draft);

  Future<List<FireReport>> listActiveReports();
}

/// Dio-backed [FireReportApiClient]. [dio]'s base URL is the API origin
/// (the same instance base the grid client uses); this class adds the
/// `/api/v1` prefix.
class DioFireReportApiClient
    implements FireReportApiClient, CitizenPhotoApiClient {
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
    return _parseEnvelope(
      json,
      (data) => FireReportDto.fromJson(data as Map<String, dynamic>).toDomain(),
    ).data;
  }

  @override
  Future<List<FireReport>> listActiveReports() async {
    final json = await _get('$_apiPrefix/reports');
    return _parseEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => FireReportDto.fromJson(e as Map<String, dynamic>).toDomain())
          .toList(),
    ).data;
  }

  @override
  Future<void> attachPhoto({
    required int reportId,
    required Uint8List bytes,
    required String filename,
  }) async {
    final response = await _dio.post<Map<String, dynamic>>(
      '$_apiPrefix/reports/$reportId/evidence',
      data: FormData.fromMap({
        'photo': MultipartFile.fromBytes(bytes, filename: filename),
        'consent': 'true',
      }),
    );
    if (response.data == null || response.data!['data'] is! Map<String, dynamic>) {
      throw const FormatException('The server returned an invalid photo receipt.');
    }
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
}
