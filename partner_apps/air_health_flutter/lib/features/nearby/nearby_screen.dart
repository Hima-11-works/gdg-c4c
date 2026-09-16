import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../../providers/home_providers.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/widgets.dart';

enum _SortMode {
  cleanerNow('Cleaner now'),
  cleanerSoon('Cleaner in next few hours'),
  nearest('Nearest');

  const _SortMode(this.label);
  final String label;
}

/// Nearby screen — shows nearby areas with lower expected pollution.
///
/// Sortable by cleaner-now, cleaner-soon, or nearest.
/// Each area shows name, distance, current AQI, category, forecast,
/// trend, and confidence.
class NearbyScreen extends ConsumerStatefulWidget {
  const NearbyScreen({super.key});

  @override
  ConsumerState<NearbyScreen> createState() => _NearbyScreenState();
}

class _NearbyScreenState extends ConsumerState<NearbyScreen> {
  _SortMode _sort = _SortMode.cleanerNow;

  @override
  Widget build(BuildContext context) {
    final areas = ref.watch(nearbyAreasProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Nearby Areas')),
      body: areas.when(
        loading: () => const LoadingState(),
        error: (e, _) => ErrorState(
          title: 'Could not load nearby areas',
          message: e.toString(),
          onRetry: () => ref.invalidate(nearbyAreasProvider),
        ),
        data: (allAreas) {
          if (allAreas.isEmpty) {
            return const EmptyState(
              icon: Icons.location_on_outlined,
              title: 'No nearby areas',
              message:
                  'Nearby pollution data will appear here when available.',
            );
          }

          final sorted = _sortAreas(allAreas, _sort);

          return ListView(
            padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
            children: [
              // ── Heading ──────────────────────────────────────────
              const SectionHeader(
                title: 'Nearby areas with lower expected pollution',
                subtitle:
                    'Environmental conditions may change. This is '
                    'informational — not a recommendation to relocate.',
              ),

              // ── Sort selector ────────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.xl,
                  vertical: AppSpacing.sm,
                ),
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: _SortMode.values.map((mode) {
                      final isSelected = mode == _sort;
                      return Padding(
                        padding: const EdgeInsets.only(right: AppSpacing.sm),
                        child: GestureDetector(
                          onTap: () => setState(() => _sort = mode),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: AppSpacing.lg,
                              vertical: AppSpacing.sm,
                            ),
                            decoration: BoxDecoration(
                              color: isSelected
                                  ? AppColors.info.withValues(alpha: 0.15)
                                  : AppColors.surfaceContainer,
                              borderRadius: BorderRadius.circular(8),
                              border: Border.all(
                                color: isSelected
                                    ? AppColors.info
                                    : AppColors.outlineVariant,
                                width: 0.5,
                              ),
                            ),
                            child: Text(
                              mode.label,
                              style: AppTypography.labelMedium.copyWith(
                                color: isSelected
                                    ? AppColors.info
                                    : AppColors.onSurfaceMuted,
                              ),
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ),
              ),

              // ── Area list ────────────────────────────────────────
              ...sorted.map((area) => _NearbyAreaCard(area: area)),
            ],
          );
        },
      ),
    );
  }

  List<NearbyArea> _sortAreas(List<NearbyArea> areas, _SortMode mode) {
    final sorted = List<NearbyArea>.from(areas);
    switch (mode) {
      case _SortMode.cleanerNow:
        sorted.sort((a, b) => a.aqiNow.compareTo(b.aqiNow));
      case _SortMode.cleanerSoon:
        sorted.sort((a, b) {
          final aForecast = _avgForecastAqi(a);
          final bForecast = _avgForecastAqi(b);
          return aForecast.compareTo(bForecast);
        });
      case _SortMode.nearest:
        sorted.sort((a, b) => a.distanceKm.compareTo(b.distanceKm));
    }
    return sorted;
  }

  double _avgForecastAqi(NearbyArea area) {
    if (area.forecast.isEmpty) return area.aqiNow.toDouble();
    final take = area.forecast.take(3).toList();
    return take.map((f) => f.aqiCpcb).reduce((a, b) => a + b) /
        take.length;
  }
}

// ── Area card ──────────────────────────────────────────────────────────

class _NearbyAreaCard extends StatelessWidget {
  const _NearbyAreaCard({required this.area});

  final NearbyArea area;

  @override
  Widget build(BuildContext context) {
    final cat = CpcbCategory.fromAqi(area.aqiNow);
    final color = AppColors.forCategory(cat);
    final trendChip = switch (area.trend) {
      AreaTrend.improving => const TrendChip.improving(),
      AreaTrend.stable => const TrendChip.stable(),
      AreaTrend.worsening => const TrendChip.worsening(),
    };

    // Best forecast AQI in the next 3 hours.
    final forecastPoints = area.forecast.take(3).toList();
    final avgForecast = forecastPoints.isEmpty
        ? null
        : (forecastPoints.map((f) => f.aqiCpcb).reduce((a, b) => a + b) /
                forecastPoints.length)
            .round();

    return AppCard(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header: AQI badge + name + distance + trend ─────────
          Row(
            children: [
              // AQI badge.
              Semantics(
                label: 'AQI ${area.aqiNow}, ${cat.label}',
                child: Container(
                  width: 52,
                  height: 52,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    '${area.aqiNow}',
                    style: AppTypography.headlineSmall.copyWith(color: color),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.xl),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(area.name, style: AppTypography.titleMedium),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      Formatters.distance(area.distanceKm),
                      style: AppTypography.bodySmall.copyWith(
                        color: AppColors.onSurfaceMuted,
                      ),
                    ),
                  ],
                ),
              ),
              trendChip,
            ],
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Detail row ──────────────────────────────────────────
          Container(
            padding: const EdgeInsets.all(AppSpacing.md),
            decoration: BoxDecoration(
              color: AppColors.surfaceDim,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                _DetailColumn(
                  label: 'Category',
                  value: cat.label,
                  color: color,
                ),
                if (avgForecast != null) ...[
                  const _Divider(),
                  _DetailColumn(
                    label: 'Forecast avg',
                    value: '$avgForecast',
                    color: AppColors.forCategory(
                        CpcbCategory.fromAqi(avgForecast)),
                  ),
                ],
                const _Divider(),
                _DetailColumn(
                  label: 'Confidence',
                  value: '${(area.confidence * 100).round()}%',
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _DetailColumn extends StatelessWidget {
  const _DetailColumn({
    required this.label,
    required this.value,
    this.color,
  });

  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: [
          Text(
            label,
            style: AppTypography.labelSmall.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            value,
            style: AppTypography.bodyMedium.copyWith(
              fontWeight: FontWeight.w600,
              color: color,
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 1,
      height: 28,
      color: AppColors.outlineVariant,
      margin: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
    );
  }
}
