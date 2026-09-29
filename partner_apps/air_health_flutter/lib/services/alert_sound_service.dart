import 'dart:async';
import 'package:flutter/services.dart';

/// Service responsible for playing a 5-second alert tone sequence and vibration
/// when an AQI threshold or rapid rise alert is triggered for patients.
class AlertSoundService {
  AlertSoundService();

  Timer? _timer;
  bool _isPlaying = false;

  /// Whether the 5-second alert sound is currently playing.
  bool get isPlaying => _isPlaying;

  /// Plays an alert tone sequence that lasts for 5 seconds.
  ///
  /// Combines repeated system alert sounds and haptic vibrations every 600ms,
  /// automatically terminating after 5 seconds.
  Future<void> play5SecondAlertSound({VoidCallback? onComplete}) async {
    // Cancel any currently playing sequence.
    stopAlertSound();

    _isPlaying = true;
    final stopwatch = Stopwatch()..start();

    // Initial alert sound and haptic pulse.
    try {
      await SystemSound.play(SystemSoundType.alert);
      await HapticFeedback.heavyImpact();
    } catch (_) {}

    _timer = Timer.periodic(const Duration(milliseconds: 650), (timer) async {
      if (stopwatch.elapsedMilliseconds >= 5000) {
        stopAlertSound();
        onComplete?.call();
        return;
      }
      try {
        await SystemSound.play(SystemSoundType.alert);
        await HapticFeedback.heavyImpact();
      } catch (_) {}
    });
  }

  /// Immediately stops any ongoing alert sound sequence.
  void stopAlertSound() {
    _timer?.cancel();
    _timer = null;
    _isPlaying = false;
  }
}
