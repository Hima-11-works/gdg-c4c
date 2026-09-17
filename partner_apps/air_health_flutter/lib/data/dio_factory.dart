import 'package:dio/dio.dart';

import 'api_config.dart';

/// Creates a configured [Dio] instance for the pollution API.
///
/// Sets base URL, auth header, timeouts, and common interceptors.
/// Widgets never see this — it's used only by
/// [RemotePollutionDataProvider].
Dio createPollutionDio(ApiConfig config) {
  final dio = Dio(
    BaseOptions(
      baseUrl: config.normalizedBaseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 15),
      headers: {
        'Authorization': 'Bearer ${config.apiKey}',
        'Accept': 'application/json',
      },
    ),
  );

  dio.interceptors.addAll([
    _LogInterceptor(),
  ]);

  return dio;
}

/// Minimal logging interceptor — logs requests/responses in debug mode.
class _LogInterceptor extends Interceptor {
  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    // ignore: avoid_print
    print('[API] ${options.method} ${options.uri}');
    handler.next(options);
  }

  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    // ignore: avoid_print
    print('[API] ${response.statusCode} ${response.requestOptions.uri}');
    handler.next(response);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    // ignore: avoid_print
    print('[API] ERROR ${err.type}: ${err.message}');
    handler.next(err);
  }
}
