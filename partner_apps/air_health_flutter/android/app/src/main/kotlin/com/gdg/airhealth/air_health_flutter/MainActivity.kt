package com.gdg.airhealth.air_health_flutter

import android.app.Activity
import android.content.Intent
import android.speech.RecognizerIntent
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterActivity
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val voiceChannel = "air_health/voice_input"
    private val speechRequestCode = 64021
    private var pendingSpeechResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, voiceChannel)
            .setMethodCallHandler { call, result ->
                if (call.method != "recognize") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
                if (pendingSpeechResult != null) {
                    result.error("busy", "Speech recognition is already active", null)
                    return@setMethodCallHandler
                }
                val localeId = call.argument<String>("localeId") ?: "en-IN"
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, localeId)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, localeId)
                    putExtra(RecognizerIntent.EXTRA_PROMPT, "Speak your report notes")
                    putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                }
                if (intent.resolveActivity(packageManager) == null) {
                    result.error("unavailable", "No speech recognition service is available", null)
                    return@setMethodCallHandler
                }
                pendingSpeechResult = result
                try {
                    startActivityForResult(intent, speechRequestCode)
                } catch (error: Exception) {
                    pendingSpeechResult = null
                    result.error("unavailable", "Could not open speech recognition", null)
                }
            }
    }

    @Deprecated("Deprecated in Android, retained for FlutterActivity result forwarding")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != speechRequestCode) return

        val result = pendingSpeechResult
        pendingSpeechResult = null
        if (result == null) return
        if (resultCode != Activity.RESULT_OK) {
            result.success(null)
            return
        }
        val transcript = data
            ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
            ?.firstOrNull()
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
        result.success(transcript)
    }
}
