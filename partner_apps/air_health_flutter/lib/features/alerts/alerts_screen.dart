import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../../providers/home_providers.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/widgets.dart';

/// Alerts screen — shows active alerts with "Why did I receive this?"
/// explanations.
///
/// For the MVP this shows pollution events. Once the alert engine is
/// wired to notifications, this will show AlertDecision history.
class AlertsScreen extends ConsumerWidget {
  const AlertsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final events = ref.watch(pollutionEventsProvider);
    final airQuality = ref.watch(currentAirQualityProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Alerts')),
      body: events.when(
        loading: () => const LoadingState(),
        error: (e, _) => ErrorState(
          title: 'Could not load alerts',
          message: e.toString(),
          onRetry: () => ref.invalidate(pollutionEventsProvider),
        ),
        data: (evts) {
          final reading = airQuality.valueOrNull;
          final currentAlerts = <_AlertItem>[];

          // Current AQI-based alert.
          if (reading != null && reading.isPoorOrWorse) {
            currentAlerts.add(_AlertItem(
              severity: reading.category == CpcbCategory.severe ||
                      reading.category == CpcbCategory.veryPoor
                  ? AlertSeverity.urgent
                  : AlertSeverity.warning,
              title: 'Current: ${reading.category.label}',
              message:
                  'Air quality is currently ${reading.category.label} '
                  '(AQI ${reading.aqiCpcb}).',
              explanation:
                  'This alert was generated because the current AQI '
                  '(${reading.aqiCpcb}) exceeds the ${reading.category.label} '
                  'threshold for your sensitivity level.',
              time: reading.recordedAt,
            ));
          }

          // Pollution event alerts.
          for (final event in evts) {
            currentAlerts.add(_AlertItem(
              severity: AlertSeverity.warning,
              title: 'Approaching: ${event.sourceArea}',
              message: event.description,
              explanation:
                  'This alert was generated because pollution from '
                  '${event.sourceArea} (estimated peak AQI '
                  '${event.peakAqiEstimate}) is expected to reach your area '
                  'around ${Formatters.time(event.expectedArrivalAt)}.',
              time: event.expectedArrivalAt,
            ));
          }

          if (currentAlerts.isEmpty) {
            return const EmptyState(
              icon: Icons.notifications_none_outlined,
              title: 'No active alerts',
              message:
                  'Alerts will appear here when air quality changes '
                  'significantly in your area.',
            );
          }

          return ListView.builder(
            padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
            itemCount: currentAlerts.length,
            itemBuilder: (_, i) => _AlertTile(alert: currentAlerts[i]),
          );
        },
      ),
    );
  }
}

class _AlertItem {
  const _AlertItem({
    required this.severity,
    required this.title,
    required this.message,
    required this.explanation,
    required this.time,
  });

  final AlertSeverity severity;
  final String title;
  final String message;
  final String explanation;
  final DateTime time;
}

class _AlertTile extends StatelessWidget {
  const _AlertTile({required this.alert});

  final _AlertItem alert;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(alert.title, style: AppTypography.titleMedium),
              ),
              StatusChip(
                label: Formatters.relativeDuration(
                  alert.time.difference(DateTime.now()),
                ),
                color: _colorForSeverity(alert.severity),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Text(alert.message, style: AppTypography.bodyMedium),
          const SizedBox(height: AppSpacing.lg),
          // "Why did I receive this?" explanation.
          GestureDetector(
            onTap: () => _showExplanation(context),
            child: Text(
              'Why did I receive this?',
              style: AppTypography.labelMedium.copyWith(
                color: AppColors.info,
              ),
            ),
          ),
        ],
      ),
    );
  }

  void _showExplanation(BuildContext context) {
    showModalBottomSheet(
      context: context,
      builder: (_) => Padding(
        padding: AppSpacing.allXxl,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Why this alert?', style: AppTypography.headlineSmall),
            const SizedBox(height: AppSpacing.lg),
            Text(alert.explanation, style: AppTypography.bodyMedium),
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
}
