import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/services/voice_input.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('air_health/voice_input');

  tearDown(() => channel.setMockMethodCallHandler(null));

  test('recognize sends the selected language and returns a transcript', () async {
    Map<Object?, Object?>? arguments;
    channel.setMockMethodCallHandler((call) async {
      expect(call.method, 'recognize');
      arguments = call.arguments as Map<Object?, Object?>;
      return 'धुआँ सड़क की ओर जा रहा है';
    });

    final transcript = await const VoiceInputClient().recognize(localeId: 'hi-IN');

    expect(arguments?['localeId'], 'hi-IN');
    expect(transcript, 'धुआँ सड़क की ओर जा रहा है');
  });

  test('recognize can be cancelled without a transcript', () async {
    channel.setMockMethodCallHandler((_) async => null);

    final transcript = await const VoiceInputClient().recognize(localeId: 'pa-IN');

    expect(transcript, isNull);
  });
}
