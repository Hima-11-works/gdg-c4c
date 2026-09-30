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
/// ```
class ApiConfig {
  const ApiConfig({
    required this.baseUrl,
    this.apiKey,
  });

  /// Default production backend deployed on Vercel.
  static const defaultProductionBaseUrl = 'https://air-health-api.vercel.app';

  /// Read from environment, defaulting to the deployed backend in every build mode.
  /// Set `POLLUTION_API_BASE_URL` to use a local or staging API.
  factory ApiConfig.fromEnvironment() {
    const envUrl = String.fromEnvironment('POLLUTION_API_BASE_URL');
    const apiKey = String.fromEnvironment('POLLUTION_API_KEY');
    return ApiConfig(
      baseUrl: envUrl.isNotEmpty ? envUrl : defaultProductionBaseUrl,
      apiKey: apiKey.isEmpty ? null : apiKey,
    );
  }

  /// Safe factory used by providers that permit a missing or invalid config.
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
