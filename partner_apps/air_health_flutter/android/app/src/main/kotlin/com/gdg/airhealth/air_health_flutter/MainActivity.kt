package com.gdg.airhealth.air_health_flutter

import android.app.Activity
import android.content.Intent
import android.provider.OpenableColumns
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Host side of the evidence photo picker.
 *
 * `air_health_flutter` attaches a photo to a fire report through
 * POST /api/v1/reports/{id}/evidence, and Dart needs the bytes to build that
 * multipart body (see lib/services/evidence_photo_picker.dart). Rather than
 * take a plugin dependency, the sheet asks the host for the file through this
 * channel and the host reads it once.
 *
 * Two deliberate limits:
 *  - the size is checked *before* the bytes are read, so picking a 40 MB photo
 *    cannot exhaust memory on the way to the server's 5 MiB refusal;
 *  - the content type comes from the content resolver, not from the file
 *    extension, because the extension is exactly the thing a renamed file
 *    lies about. The backend sniffs the bytes itself either way.
 */
class MainActivity : FlutterActivity() {

    private companion object {
        const val CHANNEL = "air_health/evidence_photo"
        const val PICK_REQUEST = 0x1E01

        /** Mirrors CITIZEN_MEDIA_MAX_BYTES; refuse before reading, not after. */
        const val MAX_PHOTO_BYTES = 5L * 1024 * 1024
    }

    /** One picker at a time: a second tap while the chooser is open is an
     * error, not a second chooser. */
    private var pendingResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "pickPhoto" -> {
                        if (pendingResult != null) {
                            result.error("busy", "A photo picker is already open.", null)
                        } else {
                            pendingResult = result
                            startActivityForResult(buildPickIntent(), PICK_REQUEST)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun buildPickIntent(): Intent =
        Intent(Intent.ACTION_GET_CONTENT).apply {
            type = "image/*"
            addCategory(Intent.CATEGORY_OPENABLE)
        }

    @Deprecated("Deprecated in Android, still the simplest correct path here")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != PICK_REQUEST) {
            super.onActivityResult(requestCode, resultCode, data)
            return
        }
        val reply = pendingResult
        pendingResult = null
        if (reply == null) return

        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) {
            // Cancelled is not a failure: null means "no photo chosen".
            reply.success(null)
            return
        }

        try {
            val declaredLength = contentResolver
                .openAssetFileDescriptor(uri, "r")?.use { it.length } ?: -1L
            if (declaredLength > MAX_PHOTO_BYTES) {
                reply.error(
                    "too_large",
                    "That photo is larger than ${MAX_PHOTO_BYTES / (1024 * 1024)} MiB. " +
                        "Pick a smaller one.",
                    null
                )
                return
            }

            val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
            if (bytes == null) {
                reply.error("unreadable", "The selected photo could not be read.", null)
                return
            }

            reply.success(
                mapOf(
                    "name" to displayNameOf(uri),
                    "contentType" to (contentResolver.getType(uri) ?: "application/octet-stream"),
                    "bytes" to bytes
                )
            )
        } catch (error: Exception) {
            reply.error("unreadable", "The selected photo could not be read.", error.message)
        }
    }

    private fun displayNameOf(uri: android.net.Uri): String {
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { cursor ->
                val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (index >= 0 && cursor.moveToFirst()) {
                    val name = cursor.getString(index)
                    if (!name.isNullOrBlank()) return name
                }
            }
        return uri.lastPathSegment ?: "photo"
    }
}
