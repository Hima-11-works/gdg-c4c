/// API configuration read from environment variables.
///
/// No keys or URLs are hardcoded — they come from:
/// - `POLLUTION_API_BASE_URL` (e.g. "https://api.example.com/v1")
/// - `POLLUTION_API_KEY` (Bearer token)
///
/// For Flutter, set these in `--dart-define`:
/// ```
/// flutter run --dart-define=POLLUTION_API_BASE_URL=https://...
/// flutter run --dart-define=POLLUTION_API_KEY=sk-...
/// ```
class ApiConfig {
  const ApiConfig({
    required this.baseUrl,
    required this.apiKey,
  });

  /// Read from environment — throws if missing.
  factory ApiConfig.fromEnvironment() {
    const baseUrl = String.fromEnvironment('POLLUTION_API_BASE_URL');
    const apiKey = String.fromEnvironment('POLLUTION_API_KEY');
    if (baseUrl.isEmpty) {
      throw StateError(
        'POLLUTION_API_BASE_URL not set. '
        'Pass --dart-define=POLLUTION_API_BASE_URL=https://...',
      );
    }
    if (apiKey.isEmpty) {
      throw StateError(
        'POLLUTION_API_KEY not set. '
        'Pass --dart-define=POLLUTION_API_KEY=sk-...',
      );
    }
    return ApiConfig(baseUrl: baseUrl, apiKey: apiKey);
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
  final String apiKey;

  /// Base URL without trailing slash.
  String get normalizedBaseUrl =>
      baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
}
