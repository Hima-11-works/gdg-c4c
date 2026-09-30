import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/data/api_config.dart';

void main() {
  test('uses the deployed Vercel backend when no URL override is supplied', () {
    const override = String.fromEnvironment('POLLUTION_API_BASE_URL');

    expect(
      ApiConfig.fromEnvironment().baseUrl,
      override.isEmpty ? 'https://air-health-api.vercel.app' : override,
    );
  });
}
