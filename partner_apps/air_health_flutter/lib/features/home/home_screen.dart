import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../../features/reports/citizen_sensor_sheet.dart';
import '../../features/reports/report_fire_sheet.dart';
import '../../providers/alert_providers.dart';
import '../../providers/data_providers.dart';
import '../../providers/home_providers.dart';
import '../../providers/profile_providers.dart';
import '../../storage/citizen_reports_store.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/forecast_chart.dart';
import '../../theme/widgets/widgets.dart';

/// Home screen — the primary view.
///
/// Information hierarchy:
/// 1. Location
/// 2. Current AQI (hero — largest element)
/// 3. Category label
/// 4. Trend
/// 5. Sensitivity note (if non-standard)
/// 6. Expected AQI change
/// 7. 6-hour forecast chart
/// 8. Pollution events (if any)
/// 9. Data freshness (footer)
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final location = ref.watch(resolvedLocationProvider);
    final airQuality = ref.watch(currentAirQualityProvider);
    final forecast = ref.watch(forecastProvider);
    final events = ref.watch(pollutionEventsProvider);
    final freshness = ref.watch(dataFreshnessProvider);
    final trend = ref.watch(trendProvider);
    final crossing = ref.watch(nextCategoryCrossingProvider);
    final profile = ref.watch(userProfileProvider);
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Air Health'),
        actions: [
          IconButton(
            tooltip: 'Citizen Hotspot & Photo Report',
            icon: const Icon(Icons.add_a_photo_outlined),
            onPressed: () => _openReportSheet(context),
          ),
        ],
      ),
      // Citizen fire reports need a backend to talk to; in dummy mode there
      // is nowhere to send one, so the entry point hides itself.
      floatingActionButton: ref.watch(fireReportApiClientProvider) == null
          ? null
          : Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                FloatingActionButton.extended(
                  heroTag: 'report-fire',
                  onPressed: () => _openReportSheet(context),
                  icon: const Icon(Icons.local_fire_department),
                  label: const Text('Report fire'),
                ),
                const SizedBox(height: 12),
                FloatingActionButton.extended(
                  heroTag: 'report-sensor-reading',
                  onPressed: () => _openSensorSheet(context),
                  icon: const Icon(Icons.sensors_outlined),
                  label: const Text('Share sensor reading'),
                ),
              ],
            ),
      body: airQuality.when(
        loading: () => const LoadingState(message: 'Loading air quality…'),
        error: (e, _) => ErrorState(
          title: 'Could not load air quality',
          message: e.toString(),
          onRetry: () => ref.invalidate(currentAirQualityProvider),
        ),
        data: (reading) {
          return RefreshIndicator(
            onRefresh: () async {
              HapticFeedback.mediumImpact();
              try {
                await ref.read(alertCoordinatorProvider).refreshAndEvaluate();
              } catch (_) {
                // The home providers still refresh and show their own error
                // states when the alert engine cannot evaluate this snapshot.
                ref.invalidate(currentAirQualityProvider);
                ref.invalidate(forecastProvider);
                ref.invalidate(pollutionEventsProvider);
                ref.invalidate(dataFreshnessProvider);
              }
            },
            child: ListView(
              padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
              children: [
                // ── Location ────────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.xl, AppSpacing.xl, AppSpacing.xl, AppSpacing.sm,
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.location_on_outlined,
                          size: 16, color: AppColors.onSurfaceMuted),
                      const SizedBox(width: AppSpacing.sm),
                      Text(
                        location.isFallback
                            ? 'Fallback location · ${location.label ?? "Bhubaneswar"}'
                            : (location.label ?? 'Current location'),
                        style: AppTypography.bodyMedium.copyWith(
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),

                // ── AQI hero ───────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: AppSpacing.xxl),
                  child: AnimatedSwitcher(
                    duration: const Duration(milliseconds: 350),
                    // Cross-fade + scale when the AQI changes (refresh, or the
                    // dev simulator advancing its clock).
                    transitionBuilder: (child, animation) => FadeTransition(
                      opacity: animation,
                      child: ScaleTransition(scale: animation, child: child),
                    ),
                    child: AqiBadge(
                      key: ValueKey<int>(reading.aqiCpcb),
                      aqi: reading.aqiCpcb,
                      category: reading.category,
                    ),
                  ),
                ),

                // ── Trend ──────────────────────────────────────────
                Center(
                  child: trend.when(
                    loading: () => const InlineLoader(),
                    error: (_, _) => const SizedBox.shrink(),
                    data: (t) {
                      return switch (t) {
                        AreaTrend.improving => const TrendChip.improving(),
                        AreaTrend.stable => const TrendChip.stable(),
                        AreaTrend.worsening => const TrendChip.worsening(),
                      };
                    },
                  ),
                ),

                // ── Sensitivity note ───────────────────────────────
                profile.when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (p) {
                    if (p == null || p.sensitivity == AlertSensitivity.standard) {
                      return const SizedBox.shrink();
                    }
                    return Padding(
                      padding: const EdgeInsets.fromLTRB(
                        AppSpacing.xl, AppSpacing.lg, AppSpacing.xl, 0,
                      ),
                      child: StatusChip(
                        label: '${p.sensitivity.label} alerts enabled',
                        color: AppColors.info,
                        icon: Icons.shield_outlined,
                      ),
                    );
                  },
                ),

                // ── Expected AQI change ────────────────────────────
                crossing.when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (c) {
                    if (c == null) return const SizedBox.shrink();
                    return Padding(
                      padding: const EdgeInsets.only(top: AppSpacing.lg),
                      child: ForecastStatus(message: c.message),
                    );
                  },
                ),

                // ── 6-hour forecast chart ──────────────────────────
                const SectionHeader(title: '6-Hour Forecast'),
                forecast.when(
                  loading: () => const Padding(
                    padding: EdgeInsets.all(AppSpacing.xl),
                    child: InlineLoader(),
                  ),
                  error: (e, _) => Padding(
                    padding: const EdgeInsets.all(AppSpacing.xl),
                    child: Text(
                      'Forecast unavailable',
                      style: AppTypography.bodyMedium.copyWith(
                        color: cs.onSurfaceVariant,
                      ),
                    ),
                  ),
                  data: (points) {
                    if (points.isEmpty) {
                      return ForecastChart(
                        currentAqi: reading.aqiCpcb,
                        forecast: const [],
                        now: reading.recordedAt,
                      );
                    }
                    final evts = events.valueOrNull ?? const [];
                    return ForecastChart(
                      currentAqi: reading.aqiCpcb,
                      forecast: points,
                      now: reading.recordedAt,
                      event: evts.isNotEmpty ? evts.first : null,
                    );
                  },
                ),

                // ── Pollution events ───────────────────────────────
                events.when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (evts) {
                    if (evts.isEmpty) return const SizedBox.shrink();
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const SectionHeader(title: 'Approaching Pollution'),
                        ...evts.map((evt) => _PollutionEventCard(
                              event: evt,
                              now: reading.recordedAt,
                            )),
                      ],
                    );
                  },
                ),

                // ── Citizen Hotspot Reports ─────────────────────────
                ref.watch(citizenReportsProvider).when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (reports) {
                    if (reports.isEmpty) return const SizedBox.shrink();
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const SectionHeader(title: 'Recent Citizen Hotspots'),
                        ...reports.take(3).map((r) => _CitizenReportCard(report: r)),
                      ],
                    );
                  },
                ),

                // ── Data freshness ─────────────────────────────────
                freshness.when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (f) => Padding(
                    padding: const EdgeInsets.only(top: AppSpacing.xxl),
                    child: _FreshnessBanner(freshness: f),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  void _openReportSheet(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => const ReportFireSheet(),
    );
  }

  Future<void> _openSensorSheet(BuildContext context) async {
    final submitted = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => const CitizenSensorSheet(),
    );
    if (submitted == true && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Reading submitted for authority review; it will not change the forecast.'),
        ),
      );
    }
  }
}

// ── Pollution event card ───────────────────────────────────────────────

class _PollutionEventCard extends StatelessWidget {
  const _PollutionEventCard({required this.event, this.now});

  final PollutionEvent event;

  /// Reference time for the lead-time chip; falls back to the wall clock.
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final lead = event.expectedArrivalAt.difference(now ?? DateTime.now());

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
                child: Text(
                  event.sourceArea,
                  style: AppTypography.titleMedium.copyWith(color: cs.onSurface),
                ),
              ),
              StatusChip(
                label: Formatters.relativeDuration(lead),
                color: AppColors.warning,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'Peak AQI ~${event.peakAqiEstimate} · ${event.description}',
            style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

// ── Freshness banner ───────────────────────────────────────────────────

class _FreshnessBanner extends StatelessWidget {
  const _FreshnessBanner({required this.freshness});

  final DataFreshness freshness;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final isStale = freshness.isStaleAt(DateTime.now());
    final quality = isStale ? DataQuality.stale : freshness.quality;
    final color = switch (quality) {
      DataQuality.full => cs.onSurfaceVariant,
      DataQuality.partial => AppColors.warning,
      DataQuality.forecastUnavailable => AppColors.warning,
      DataQuality.stale => AppColors.error,
    };
    final icon = switch (quality) {
      DataQuality.full => Icons.check_circle_outline,
      DataQuality.partial => Icons.info_outline,
      DataQuality.forecastUnavailable => Icons.info_outline,
      DataQuality.stale => Icons.warning_amber_outlined,
    };
    final demo = freshness.isDemo || freshness.mode == 'demo';
    final modeLabel = demo
        ? 'DEMO SIM'
        : freshness.mode == 'mixed'
            ? 'MIXED DATA'
            : freshness.mode == 'live'
                ? 'LIVE DATA'
                : 'DATA MODE UNKNOWN';
    final prefix = '${isStale ? "STALE · " : ""}$modeLabel · ';

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      child: Row(
        children: [
          Icon(icon, size: 14, color: color),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              '$prefix${quality.label} · Updated ${Formatters.relativeDuration(freshness.age)}',
              style: AppTypography.labelSmall.copyWith(color: color),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}

// ── Citizen Report card ───────────────────────────────────────────────

class _CitizenReportCard extends ConsumerWidget {
  const _CitizenReportCard({required this.report});

  final FireReport report;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cs = Theme.of(context).colorScheme;

    return AppCard(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (report.photoBytes != null && report.photoBytes!.isNotEmpty)
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: Image.memory(
                report.photoBytes!,
                width: 68,
                height: 68,
                fit: BoxFit.cover,
                errorBuilder: (_, __, ___) => Container(
                  width: 68,
                  height: 68,
                  color: cs.surfaceContainerHighest,
                  child: const Icon(Icons.broken_image, size: 24),
                ),
              ),
            )
          else
            Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                color: cs.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(Icons.local_fire_department, color: cs.primary),
            ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        report.kind.label,
                        style: AppTypography.titleMedium.copyWith(color: cs.onSurface),
                      ),
                    ),
                    StatusChip(
                      label: 'Smoke ${report.smokeIntensity}/5',
                      color: AppColors.warning,
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  '${report.region ?? "Current region"} · ${Formatters.relativeDuration(DateTime.now().difference(report.reportedAt))} ago',
                  style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
                ),
                const SizedBox(height: AppSpacing.sm),
                _ReportReviewStatus(reportId: report.id),
                if (report.notes != null && report.notes!.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    report.notes!,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: AppTypography.bodySmall.copyWith(color: cs.onSurface),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ReportReviewStatus extends ConsumerWidget {
  const _ReportReviewStatus({required this.reportId});

  final int reportId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(citizenReportReviewStatusProvider(reportId));
    return status.when(
      loading: () => const Row(
        children: [
          SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          SizedBox(width: AppSpacing.sm),
          Text('Checking review status…'),
        ],
      ),
      error: (_, _) => Row(
        children: [
          Expanded(
            child: Text(
              'Review status unavailable · check your connection',
              style: AppTypography.bodySmall.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          IconButton(
            tooltip: 'Retry status check',
            visualDensity: VisualDensity.compact,
            onPressed: () => ref.invalidate(
              citizenReportReviewStatusProvider(reportId),
            ),
            icon: const Icon(Icons.refresh, size: 18),
          ),
        ],
      ),
      data: (value) {
        if (value == null) {
          return const Text('Review status is not available from this server.');
        }
        final color = switch (value.status) {
          'corroborated' => AppColors.aqiGood,
          'rejected' || 'expired' => AppColors.error,
          'under_review' => AppColors.info,
          _ => AppColors.warning,
        };
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: StatusChip(
                    label: value.displayStatus,
                    color: color,
                    icon: value.status == 'corroborated'
                        ? Icons.verified_outlined
                        : value.status == 'rejected'
                            ? Icons.cancel_outlined
                            : Icons.hourglass_top,
                  ),
                ),
                IconButton(
                  tooltip: 'Refresh review status',
                  visualDensity: VisualDensity.compact,
                  onPressed: () => ref.invalidate(
                    citizenReportReviewStatusProvider(reportId),
                  ),
                  icon: const Icon(Icons.refresh, size: 18),
                ),
              ],
            ),
            Text(
              '${value.statusMeaning} ${value.affectsAirQualityModel ? "This report contributes to air-quality modeling." : "This report is not currently used in the air-quality model."}',
              style: AppTypography.bodySmall.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        );
      },
    );
  }
}
