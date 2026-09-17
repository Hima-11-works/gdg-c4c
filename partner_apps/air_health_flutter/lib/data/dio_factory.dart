import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

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

  if (kDebugMode) {
    dio.interceptors.add(_DebugLogInterceptor());
  }

  return dio;
}

/// Debug-only logging interceptor — logs requests/responses but
/// NEVER runs in release builds. Stripped by the tree-shaker.
class _DebugLogInterceptor extends Interceptor {
  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    debugPrint('[API] ${options.method} ${options.uri}');
    handler.next(options);
  }

  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    debugPrint('[API] ${response.statusCode} ${response.requestOptions.uri}');
    handler.next(response);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    debugPrint('[API] ERROR ${err.type}: ${err.message}');
    handler.next(err);
  }
}
