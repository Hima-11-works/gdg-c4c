import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/providers/scenario_data.dart';
import '../theme/app_colors.dart';
import '../theme/app_typography.dart';
import 'dev_scenario_simulator.dart';

/// Development-only scenario simulator panel.
///
/// NOT included in release builds — gated by `kDebugMode`.
/// Shows at the bottom of the screen as a floating overlay.
class DevSimulatorPanel extends ConsumerStatefulWidget {
  const DevSimulatorPanel({super.key});

  @override
  ConsumerState<DevSimulatorPanel> createState() => _DevSimulatorPanelState();
}

class _DevSimulatorPanelState extends ConsumerState<DevSimulatorPanel> {
  Timer? _autoTimer;
  bool _expanded = false;

  @override
  void dispose() {
    _autoTimer?.cancel();
    super.dispose();
  }

  void _toggleAutoPlay() {
    final playing = ref.read(simulatorPlayingProvider);
    if (playing) {
      _autoTimer?.cancel();
      _autoTimer = null;
      ref.read(simulatorPlayingProvider.notifier).state = false;
    } else {
      ref.read(simulatorPlayingProvider.notifier).state = true;
      _autoTimer = Timer.periodic(
        const Duration(milliseconds: 750),
        (_) {
          if (!mounted) return;
          final stillPlaying = ref.read(simulatorPlayingProvider);
          if (!stillPlaying) {
            _autoTimer?.cancel();
            _autoTimer = null;
            return;
          }
          ref.read(simulatorTimeOffsetProvider.notifier).state +=
              const Duration(minutes: 15);
        },
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!kDebugMode) return const SizedBox.shrink();

    final scenario = ref.watch(simulatorScenarioProvider);
    final offset = ref.watch(simulatorTimeOffsetProvider);
    final playing = ref.watch(simulatorPlayingProvider);
    final now = ref.watch(simulatorNowProvider);

    return Positioned(
      left: 8,
      right: 8,
      bottom: 80,
      child: GestureDetector(
        onTap: () => setState(() => _expanded = !_expanded),
        child: Container(
          decoration: BoxDecoration(
            color: const Color(0xDD1A1D23),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppColors.outline, width: 0.5),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Header.
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
                child: Row(
                  children: [
                    const Icon(Icons.science, size: 16, color: AppColors.info),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'DEV: ${scenario.name}',
                        style: AppTypography.labelMedium.copyWith(
                          color: AppColors.info,
                        ),
                      ),
                    ),
                    Text(
                      '+${offset.inMinutes}m',
                      style: AppTypography.labelSmall.copyWith(
                        color: AppColors.onSurfaceMuted,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Icon(
                      _expanded
                          ? Icons.keyboard_arrow_down
                          : Icons.keyboard_arrow_up,
                      size: 16,
                      color: AppColors.onSurfaceMuted,
                    ),
                  ],
                ),
              ),

              if (_expanded) ...[
                const Divider(height: 1, color: AppColors.outlineVariant),

                // Scenario selector.
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
                  child: Wrap(
                    spacing: 4,
                    runSpacing: 4,
                    children: Scenario.values.map((s) {
                      final isSelected = s == scenario;
                      return GestureDetector(
                        onTap: () {
                          _autoTimer?.cancel();
                          _autoTimer = null;
                          final notifier = ref.read(
                              devScenarioSimulatorProvider.notifier);
                          notifier.switchScenario(s);
                        },
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 4,
                          ),
                          decoration: BoxDecoration(
                            color: isSelected
                                ? AppColors.info.withValues(alpha: 0.2)
                                : AppColors.surfaceContainer,
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: isSelected
                                  ? AppColors.info
                                  : AppColors.outlineVariant,
                              width: 0.5,
                            ),
                          ),
                          child: Text(
                            _scenarioLabel(s),
                            style: AppTypography.labelSmall.copyWith(
                              color: isSelected
                                  ? AppColors.info
                                  : AppColors.onSurfaceMuted,
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ),

                // Playback controls.
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
                  child: Row(
                    children: [
                      // Step back.
                      _SmallButton(
                        icon: Icons.skip_previous,
                        onTap: () {
                          final notifier = ref.read(
                              devScenarioSimulatorProvider.notifier);
                          notifier.jumpTo(Duration(
                            minutes:
                                (offset.inMinutes - 15).clamp(0, 9999),
                          ));
                        },
                      ),
                      const SizedBox(width: 4),
                      // Play/pause.
                      _SmallButton(
                        icon: playing ? Icons.pause : Icons.play_arrow,
                        onTap: _toggleAutoPlay,
                        highlight: playing,
                      ),
                      const SizedBox(width: 4),
                      // Step forward.
                      _SmallButton(
                        icon: Icons.skip_next,
                        onTap: () {
                          final notifier = ref.read(
                              devScenarioSimulatorProvider.notifier);
                          notifier.step();
                        },
                      ),
                      const SizedBox(width: 4),
                      // Reset.
                      _SmallButton(
                        icon: Icons.replay,
                        onTap: () {
                          _autoTimer?.cancel();
                          _autoTimer = null;
                          ref.read(simulatorPlayingProvider.notifier)
                              .state = false;
                          final notifier = ref.read(
                              devScenarioSimulatorProvider.notifier);
                          notifier.reset();
                        },
                      ),
                      const Spacer(),
                      // Current time display.
                      Text(
                        '${now.hour}:${now.minute.toString().padLeft(2, '0')}',
                        style: AppTypography.labelSmall.copyWith(
                          color: AppColors.onSurfaceMuted,
                        ),
                      ),
                    ],
                  ),
                ),

                // Demo play button.
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
                  child: SizedBox(
                    width: double.infinity,
                    child: GestureDetector(
                      onTap: () {
                        _autoTimer?.cancel();
                        _autoTimer = null;
                        ref.read(simulatorPlayingProvider.notifier)
                            .state = false;
                        final notifier = ref.read(
                            devScenarioSimulatorProvider.notifier);
                        notifier.switchScenario(Scenario.approachingPlume);
                        // Auto-play through the demo steps.
                        _runDemoSequence();
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        decoration: BoxDecoration(
                          color: AppColors.info.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(
                            color: AppColors.info.withValues(alpha: 0.3),
                            width: 0.5,
                          ),
                        ),
                        child: Text(
                          '▶ Run full demo sequence',
                          style: AppTypography.labelMedium.copyWith(
                            color: AppColors.info,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  void _runDemoSequence() {
    final steps = DevScenarioSimulator.demoSteps;
    var i = 0;
    _autoTimer = Timer.periodic(const Duration(seconds: 2), (timer) {
      if (!mounted || i >= steps.length) {
        timer.cancel();
        _autoTimer = null;
        ref.read(simulatorPlayingProvider.notifier).state = false;
        return;
      }
      ref.read(simulatorTimeOffsetProvider.notifier).state = steps[i];
      i++;
    });
  }

  static String _scenarioLabel(Scenario s) {
    return switch (s) {
      Scenario.cleanStable => 'Clean',
      Scenario.gradualRise => 'Rise',
      Scenario.rapidSpike => 'Spike',
      Scenario.approachingPlume => 'Plume',
      Scenario.severeNow => 'Severe',
      Scenario.recovery => 'Recovery',
      Scenario.dataUnavailable => 'No Data',
      Scenario.partialData => 'Partial',
    };
  }
}

class _SmallButton extends StatelessWidget {
  const _SmallButton({
    required this.icon,
    required this.onTap,
    this.highlight = false,
  });

  final IconData icon;
  final VoidCallback onTap;
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 32,
        height: 28,
        decoration: BoxDecoration(
          color: highlight
              ? AppColors.info.withValues(alpha: 0.2)
              : AppColors.surfaceContainer,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: highlight
                ? AppColors.info
                : AppColors.outlineVariant,
            width: 0.5,
          ),
        ),
        child: Icon(icon, size: 16, color: AppColors.onSurfaceMuted),
      ),
    );
  }
}
