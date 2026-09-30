/// API configuration read from environment variables.
///
/// Only the origin is needed — the grid API is unauthenticated:
/// - `POLLUTION_API_BASE_URL` (e.g. "http://localhost:8000")
///
/// `POLLUTION_API_KEY` is optional and only used by the legacy
/// `RemotePollutionDataProvider` contract.
///
/// For Flutter, set these in `--dart-define`:
/// ```
/// flutter run --dart-define=POLLUTION_API_BASE_URL=http://localhost:8000
import 'package:flutter/foundation.dart';

class ApiConfig {
  const ApiConfig({
    required this.baseUrl,
    this.apiKey,
  });

  /// Default production backend deployed on Vercel.
  static const defaultProductionBaseUrl = 'https://air-health-api.vercel.app';

  /// Read from environment — defaults to the deployed production backend in release mode,
  /// or when POLLUTION_API_BASE_URL is explicitly provided.
  factory ApiConfig.fromEnvironment() {
    const envUrl = String.fromEnvironment('POLLUTION_API_BASE_URL');
    final baseUrl = envUrl.isNotEmpty
        ? envUrl
        : (kReleaseMode ? defaultProductionBaseUrl : '');
    const apiKey = String.fromEnvironment('POLLUTION_API_KEY');
    if (baseUrl.isEmpty) {
      throw StateError(
        'POLLUTION_API_BASE_URL not set. '
        'Pass --dart-define=POLLUTION_API_BASE_URL=https://air-health-api.vercel.app',
      );
    }
    return ApiConfig(
      baseUrl: baseUrl,
      apiKey: apiKey.isEmpty ? null : apiKey,
    );
  }

  /// Safe factory — returns null if env vars are missing.
  static ApiConfig? tryFromEnvironment() {
    try {
      return ApiConfig.fromEnvironment();
    } catch (_) {
      return null;
    }
  }

  final String baseUrl;

  /// Optional Bearer token — the grid API does not require one.
  final String? apiKey;

  /// Base URL without trailing slash.
  String get normalizedBaseUrl =>
      baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
}
