import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/formatters.dart';
import '../domain/alert_record.dart';
import '../domain/models/models.dart';
import 'alert_providers.dart';
import 'profile_providers.dart';

/// Accumulated alert history — the Alerts screen reads from here.
///
/// Records are added when the engine produces new decisions, and
/// resolved when recovery fires. Old records (>24h, resolved) are
/// pruned automatically.
final alertHistoryProvider =
    AsyncNotifierProvider<AlertHistoryNotifier, List<AlertRecord>>(
  AlertHistoryNotifier.new,
);

class AlertHistoryNotifier extends AsyncNotifier<List<AlertRecord>> {
  @override
  Future<List<AlertRecord>> build() async {
    return const [];
  }

  /// Add new records from engine decisions, deduplicating by dedupKey.
  void addFromDecisions(List<AlertDecision> decisions) {
    final profile = ref.read(userProfileProvider).valueOrNull;
    final sensitivity = profile?.sensitivity ?? AlertSensitivity.standard;
    final messageService = ref.read(alertMessageServiceProvider);

    final existing = state.valueOrNull ?? [];
    final existingKeys = existing.map((r) => r.decision.dedupKey).toSet();
    final now = DateTime.now();

    final newRecords = <AlertRecord>[];
    for (final decision in decisions) {
      if (!decision.shouldAlert) continue;
      if (existingKeys.contains(decision.dedupKey)) continue;

      final message = messageService.buildMessage(
        decision: decision,
        sensitivity: sensitivity,
      );

      newRecords.add(AlertRecord(
        id: '${decision.dedupKey}_${now.millisecondsSinceEpoch}',
        decision: decision,
        title: message.title,
        body: message.body,
        guidance: message.guidance,
        explanation: _buildExplanation(decision),
        createdAt: now,
      ));
    }

    if (newRecords.isNotEmpty) {
      state = AsyncData([...newRecords, ...existing]);
    }
  }

  /// Mark records matching [dedupKey] as resolved (recovery).
  void resolveByKey(String dedupKey) {
    final existing = state.valueOrNull ?? [];
    final updated = existing.map((r) {
      if (r.decision.dedupKey == dedupKey && !r.isResolved) {
        return r.copyWith(resolvedAt: DateTime.now());
      }
      return r;
    }).toList();
    state = AsyncData(updated);
  }

  /// Prune records older than 24h that are resolved.
  void prune() {
    final existing = state.valueOrNull ?? [];
    final now = DateTime.now();
    final kept = existing.where((r) {
      if (r.isResolved && now.difference(r.createdAt).inHours > 24) {
        return false;
      }
      if (!r.isResolved && !r.isActive && !r.isRecent) {
        return false;
      }
      return true;
    }).toList();
    if (kept.length != existing.length) {
      state = AsyncData(kept);
    }
  }

  /// Clear all history.
  void clear() {
    state = const AsyncData([]);
  }

  static String _buildExplanation(AlertDecision d) {
    return switch (d.trigger) {
      AlertTrigger.currentThreshold =>
        'Air quality near you reached ${Formatters.categoryLabel(d.currentAqi)} '
        '(AQI ${d.currentAqi}), which exceeds the alert threshold '
        'for your sensitivity setting.',
      AlertTrigger.forecastThreshold =>
        'Air quality is expected to reach ${Formatters.categoryLabel(d.predictedAqi ?? d.currentAqi)} '
        '${d.leadTime != null ? "in approximately ${Formatters.leadTime(d.leadTime!)}" : "soon"}. '
        'This triggered an early warning based on your sensitivity setting.',
      AlertTrigger.rapidRise =>
        'Air quality is rising quickly — current AQI ${d.currentAqi} '
        'is expected to reach ${d.predictedAqi ?? "?"} within a few hours. '
        'This rate of change triggered an alert.',
      AlertTrigger.approachingPollution =>
        'Pollution from a nearby area (estimated peak AQI '
        '${d.predictedAqi ?? "?"}) is expected to reach your area '
        '${d.leadTime != null ? "in approximately ${Formatters.leadTime(d.leadTime!)}" : "soon"}.',
      AlertTrigger.recovery =>
        'Air quality has improved to ${Formatters.categoryLabel(d.currentAqi)} '
        '(AQI ${d.currentAqi}), below the previous alert threshold.',
    };
  }
}
