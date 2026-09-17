import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/formatters.dart';
import '../../domain/alert_record.dart';
import '../../domain/models/models.dart';
import '../../providers/alert_history_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/widgets.dart';

/// Alerts screen — Active / Recent / Resolved sections.
///
/// Each alert shows title, time, severity, AQI details, reason,
/// confidence, and a "Why did I receive this?" explanation.
class AlertsScreen extends ConsumerWidget {
  const AlertsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final historyAsync = ref.watch(alertHistoryProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Alerts'),
        actions: [
          historyAsync.whenOrNull(
                data: (records) => records.isNotEmpty
                    ? TextButton(
                        onPressed: () =>
                            ref.read(alertHistoryProvider.notifier).clear(),
                        child: Text(
                          'Clear',
                          style: AppTypography.labelMedium.copyWith(
                            color: AppColors.onSurfaceMuted,
                          ),
                        ),
                      )
                    : null,
              ) ??
              const SizedBox.shrink(),
        ],
      ),
      body: historyAsync.when(
        loading: () => const LoadingState(),
        error: (e, _) => ErrorState(
          title: 'Could not load alerts',
          message: e.toString(),
          onRetry: () => ref.invalidate(alertHistoryProvider),
        ),
        data: (records) {
          final active =
              records.where((r) => r.isActive).toList();
          final recent =
              records.where((r) => r.isRecent).toList();
          final resolved =
              records.where((r) => r.isResolvedSection).toList();

          if (records.isEmpty) {
            return const EmptyState(
              icon: Icons.notifications_none_outlined,
              title: 'No alerts yet',
              message:
                  'Alerts will appear here when air quality changes '
                  'significantly in your area.',
            );
          }

          return ListView(
            padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
            children: [
              if (active.isNotEmpty) ...[
                const SectionHeader(
                  title: 'Active',
                  subtitle: 'Conditions currently require your attention.',
                ),
                ...active.map((r) => _AlertCard(record: r)),
              ],
              if (recent.isNotEmpty) ...[
                const SectionHeader(
                  title: 'Recent',
                  subtitle: 'From the past 24 hours.',
                ),
                ...recent.map((r) => _AlertCard(record: r)),
              ],
              if (resolved.isNotEmpty) ...[
                const SectionHeader(
                  title: 'Resolved',
                  subtitle: 'Conditions have improved.',
                ),
                ...resolved.map((r) => _AlertCard(record: r)),
              ],
            ],
          );
        },
      ),
    );
  }
}

// ── Alert card ─────────────────────────────────────────────────────────

class _AlertCard extends StatelessWidget {
  const _AlertCard({required this.record});

  final AlertRecord record;

  @override
  Widget build(BuildContext context) {
    final color = _colorForSeverity(record.severity);

    return AppCard(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header: title + severity + time ──────────────────────
          Row(
            children: [
              Icon(_iconForSeverity(record.severity), size: 18, color: color),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(record.title, style: AppTypography.titleMedium),
              ),
              StatusChip(
                label: _severityLabel(record.severity),
                color: color,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            Formatters.relativeDuration(
              record.createdAt.difference(DateTime.now()),
            ),
            style: AppTypography.labelSmall.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Body ────────────────────────────────────────────────
          Text(record.body, style: AppTypography.bodyMedium),

          const SizedBox(height: AppSpacing.lg),

          // ── Details grid ────────────────────────────────────────
          _DetailGrid(record: record),

          const SizedBox(height: AppSpacing.lg),

          // ── "Why did I receive this?" ───────────────────────────
          GestureDetector(
            onTap: () => _showExplanation(context),
            child: Text(
              'Why did I receive this?',
              style: AppTypography.labelMedium.copyWith(
                color: AppColors.info,
              ),
            ),
          ),

          // ── Guidance ────────────────────────────────────────────
          if (record.guidance.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.md),
            Container(
              padding: const EdgeInsets.all(AppSpacing.md),
              decoration: BoxDecoration(
                color: AppColors.surfaceDim,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.tips_and_updates_outlined,
                      size: 16, color: AppColors.onSurfaceMuted),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: Text(
                      record.guidance,
                      style: AppTypography.bodySmall.copyWith(
                        color: AppColors.onSurfaceMuted,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  void _showExplanation(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (_) => Padding(
        padding: const EdgeInsets.fromLTRB(24, 24, 24, 48),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(_iconForSeverity(record.severity),
                    size: 24, color: _colorForSeverity(record.severity)),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(record.title,
                      style: AppTypography.headlineSmall),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xxl),
            Text('Why this alert?',
                style: AppTypography.titleMedium.copyWith(
                  color: AppColors.onSurfaceMuted,
                )),
            const SizedBox(height: AppSpacing.md),
            Text(record.explanation, style: AppTypography.bodyLarge),
            const SizedBox(height: AppSpacing.xxl),
            Text('What you can do',
                style: AppTypography.titleMedium.copyWith(
                  color: AppColors.onSurfaceMuted,
                )),
            const SizedBox(height: AppSpacing.md),
            Text(record.guidance, style: AppTypography.bodyLarge),
            const SizedBox(height: AppSpacing.xxl),
          ],
        ),
      ),
    );
  }

  static Color _colorForSeverity(AlertSeverity s) {
    return switch (s) {
      AlertSeverity.info => AppColors.info,
      AlertSeverity.advisory => AppColors.aqiModerate,
      AlertSeverity.warning => AppColors.warning,
      AlertSeverity.urgent => AppColors.error,
    };
  }

  static IconData _iconForSeverity(AlertSeverity s) {
    return switch (s) {
      AlertSeverity.info => Icons.info_outline,
      AlertSeverity.advisory => Icons.info_outline,
      AlertSeverity.warning => Icons.warning_amber_outlined,
      AlertSeverity.urgent => Icons.dangerous_outlined,
    };
  }

  static String _severityLabel(AlertSeverity s) {
    return switch (s) {
      AlertSeverity.info => 'Info',
      AlertSeverity.advisory => 'Advisory',
      AlertSeverity.warning => 'Warning',
      AlertSeverity.urgent => 'Urgent',
    };
  }
}

// ── Detail grid ────────────────────────────────────────────────────────

class _DetailGrid extends StatelessWidget {
  const _DetailGrid({required this.record});

  final AlertRecord record;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: AppColors.surfaceDim,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        children: [
          _detailRow('Current AQI', '${record.currentAqi}'),
          if (record.predictedAqi != null)
            _detailRow('Expected AQI', '${record.predictedAqi}'),
          if (record.leadTime != null)
            _detailRow('Arriving in', Formatters.leadTime(record.leadTime!)),
          _detailRow('Confidence', '${(record.confidence * 100).round()}%'),
          _detailRow('Reason', _triggerLabel(record.trigger)),
        ],
      ),
    );
  }

  Widget _detailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          SizedBox(
            width: 100,
            child: Text(label,
                style: AppTypography.labelSmall.copyWith(
                  color: AppColors.onSurfaceMuted,
                )),
          ),
          Expanded(
            child: Text(value, style: AppTypography.bodySmall),
          ),
        ],
      ),
    );
  }

  static String _triggerLabel(AlertTrigger t) {
    return switch (t) {
      AlertTrigger.currentThreshold => 'Current threshold exceeded',
      AlertTrigger.forecastThreshold => 'Forecast threshold exceeded',
      AlertTrigger.rapidRise => 'Rapid AQI rise detected',
      AlertTrigger.approachingPollution => 'Pollution approaching area',
      AlertTrigger.recovery => 'Conditions improved',
    };
  }
}
