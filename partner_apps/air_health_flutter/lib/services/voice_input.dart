import 'package:flutter/services.dart';

/// Explicit, one-shot Android dictation bridge used by the citizen report
/// form. Audio is handled by the device's speech recognizer and is never
/// included in report API requests.
class VoiceInputClient {
  const VoiceInputClient();

  static const _channel = MethodChannel('air_health/voice_input');

  Future<String?> recognize({required String localeId}) {
    return _channel.invokeMethod<String>(
      'recognize',
      {'localeId': localeId},
    );
  }
}
