import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../../providers/home_providers.dart';
import '../../providers/profile_providers.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/forecast_chart.dart';
import '../../theme/widgets/widgets.dart';

/// Home screen — the primary view.
///
/// Information hierarchy:
/// 1. Current location
/// 2. Current AQI (hero)
/// 3. CPCB category
/// 4. Trend
/// 5. Personalized alert if active
/// 6. Expected AQI change
/// 7. 12-hour forecast
/// 8. Nearby pollution event
/// 9. Data freshness
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final location = ref.watch(currentLocationProvider);
    final airQuality = ref.watch(currentAirQualityProvider);
    final forecast = ref.watch(forecastProvider);
    final events = ref.watch(pollutionEventsProvider);
    final freshness = ref.watch(dataFreshnessProvider);
    final trend = ref.watch(trendProvider);
    final crossing = ref.watch(nextCategoryCrossingProvider);
    final profile = ref.watch(userProfileProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Air Health')),
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
              ref.invalidate(currentAirQualityProvider);
              ref.invalidate(forecastProvider);
              ref.invalidate(pollutionEventsProvider);
              ref.invalidate(dataFreshnessProvider);
            },
            child: ListView(
              padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
              children: [
                // ── Location ────────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.xl,
                    AppSpacing.xl,
                    AppSpacing.xl,
                    AppSpacing.sm,
                  ),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.location_on_outlined,
                        size: 18,
                        color: AppColors.onSurfaceMuted,
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      Text(
                        location.label ?? 'Current location',
                        style: AppTypography.bodyLarge.copyWith(
                          color: AppColors.onSurfaceMuted,
                        ),
                      ),
                    ],
                  ),
                ),

                // ── AQI hero ───────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.symmetric(
                    vertical: AppSpacing.xxl,
                  ),
                  child: AqiBadge(
                    aqi: reading.aqiCpcb,
                    category: reading.category,
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

                // ── Personalized sensitivity note ──────────────────
                profile.when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (p) {
                    if (p == null ||
                        p.sensitivity == AlertSensitivity.standard) {
                      return const SizedBox.shrink();
                    }
                    return Padding(
                      padding: const EdgeInsets.fromLTRB(
                        AppSpacing.xl,
                        AppSpacing.lg,
                        AppSpacing.xl,
                        0,
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

                // ── 12-hour forecast chart ───────────────────────────
                const SectionHeader(title: '12-Hour Forecast'),
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
                        color: AppColors.onSurfaceMuted,
                      ),
                    ),
                  ),
                  data: (points) {
                    if (points.isEmpty) {
                      return Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: AppSpacing.xl,
                        ),
                        child: ForecastChart(
                          currentAqi: reading.aqiCpcb,
                          forecast: const [],
                        ),
                      );
                    }
                    final evts = events.valueOrNull ?? const [];
                    return ForecastChart(
                      currentAqi: reading.aqiCpcb,
                      forecast: points,
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
                        const SectionHeader(
                          title: 'Nearby Pollution Events',
                          subtitle:
                              'Environmental information — not a recommendation to relocate.',
                        ),
                        ...evts.map((evt) => _PollutionEventCard(event: evt)),
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
}

// ── Pollution event card ───────────────────────────────────────────────

class _PollutionEventCard extends StatelessWidget {
  const _PollutionEventCard({required this.event});

  final PollutionEvent event;

  @override
  Widget build(BuildContext context) {
    final lead = event.expectedArrivalAt.difference(DateTime.now());

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
              const Icon(Icons.air, size: 18, color: AppColors.warning),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(
                  event.sourceArea,
                  style: AppTypography.titleMedium,
                ),
              ),
              StatusChip(
                label: Formatters.relativeDuration(lead),
                color: AppColors.warning,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Text(event.description, style: AppTypography.bodyMedium),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'Estimated peak AQI: ${event.peakAqiEstimate}',
            style: AppTypography.labelMedium.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
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
    final quality = freshness.quality;
    final color = switch (quality) {
      DataQuality.full => AppColors.onSurfaceMuted,
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

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      child: Row(
        children: [
          Icon(icon, size: 14, color: color),
          const SizedBox(width: AppSpacing.sm),
          Text(
            '${quality.label} · Updated ${Formatters.relativeDuration(freshness.age)}',
            style: AppTypography.labelSmall.copyWith(color: color),
          ),
        ],
      ),
    );
  }
}
