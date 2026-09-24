/// Selecting a photo to attach to a fire report.
///
/// Deliberately dependency-free: the app resolves its own packages from
/// `pubspec.lock`, and a picker plugin would have to be fetched and locked
/// before this build could be verified at all. A `MethodChannel` to the host
/// platform needs nothing new, and it keeps the upload logic testable behind
/// [EvidencePhotoPicker].
///
/// Android is implemented (see MainActivity.kt). On a platform with no host
/// implementation the call throws [EvidencePhotoPickerException] with code
/// `unsupported_platform`, which the sheet reports as an unavailable feature
/// rather than as a failure the resident caused.
library;

import 'dart:typed_data';

import 'package:flutter/services.dart';

import '../data/reports/fire_report_api.dart';

abstract class EvidencePhotoPicker {
  /// Null when the resident cancels. Throws [EvidencePhotoPickerException] when
  /// picking is impossible or the chosen file cannot be read.
  Future<EvidencePhoto?> pickPhoto();
}

class EvidencePhotoPickerException implements Exception {
  const EvidencePhotoPickerException(this.code, this.message);

  final String code;
  final String message;

  @override
  String toString() => 'EvidencePhotoPickerException($code)';
}

class MethodChannelEvidencePhotoPicker implements EvidencePhotoPicker {
  const MethodChannelEvidencePhotoPicker();

  static const MethodChannel _channel = MethodChannel('air_health/evidence_photo');

  @override
  Future<EvidencePhoto?> pickPhoto() async {
    Map<String, dynamic>? result;
    try {
      result = await _channel.invokeMapMethod<String, dynamic>('pickPhoto');
    } on MissingPluginException {
      throw const EvidencePhotoPickerException(
        'unsupported_platform',
        'Choosing a photo is not available on this platform yet. The sensor '
            'reading on its own can still be attached.',
      );
    } on PlatformException catch (error) {
      throw EvidencePhotoPickerException(
        error.code,
        error.message ?? 'The photo could not be read.',
      );
    }

    if (result == null) return null;
    final bytes = result['bytes'];
    if (bytes is! Uint8List) {
      throw const EvidencePhotoPickerException(
        'unreadable',
        'The selected photo could not be read.',
      );
    }
    return EvidencePhoto(
      name: (result['name'] as String?) ?? 'photo',
      contentType: (result['contentType'] as String?) ?? 'application/octet-stream',
      bytes: bytes,
    );
  }
}
