import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../domain/models/models.dart';
import '../../providers/prefs_providers.dart';
import '../../providers/profile_providers.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/widgets.dart';

/// Multi-step onboarding flow.
///
/// Steps:
/// 1. Welcome
/// 2. Location permission explanation
/// 3. Health context (optional)
/// 4. Alert sensitivity
/// 5. Notification permission
/// 6. Complete → Home
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  final _pageController = PageController();
  int _currentPage = 0;

  // Selections made during onboarding.
  UserHealthContext _healthContext = UserHealthContext.none;
  AlertSensitivity _sensitivity = AlertSensitivity.standard;

  static const _totalSteps = 5;

  void _next() {
    if (_currentPage < _totalSteps - 1) {
      _pageController.nextPage(
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    } else {
      _complete();
    }
  }

  void _back() {
    if (_currentPage > 0) {
      _pageController.previousPage(
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    }
  }

  Future<void> _complete() async {
    final profile = UserProfile(
      healthContext: _healthContext,
      sensitivity: _sensitivity,
    );
    await ref.read(userProfileProvider.notifier).updateProfile(profile);
    await ref.read(prefsStoreProvider).setOnboardingDone(true);
    if (mounted) context.go('/home');
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        leading: _currentPage > 0
            ? IconButton(
                icon: const Icon(Icons.arrow_back),
                onPressed: _back,
              )
            : null,
        actions: [
          if (_currentPage > 0)
            TextButton(
              onPressed: _complete,
              child: Text(
                'Skip',
                style: AppTypography.labelLarge.copyWith(
                  color: AppColors.onSurfaceMuted,
                ),
              ),
            ),
        ],
      ),
      body: Column(
        children: [
          // Progress indicator.
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.xl,
              vertical: AppSpacing.md,
            ),
            child: LinearProgressIndicator(
              value: (_currentPage + 1) / _totalSteps,
              borderRadius: BorderRadius.circular(4),
              minHeight: 4,
            ),
          ),
          Expanded(
            child: PageView(
              controller: _pageController,
              physics: const NeverScrollableScrollPhysics(),
              onPageChanged: (i) => setState(() => _currentPage = i),
              children: [
                _WelcomeStep(onNext: _next),
                _LocationStep(onNext: _next),
                _HealthContextStep(
                  selected: _healthContext,
                  onChanged: (ctx) => setState(() => _healthContext = ctx),
                  onNext: _next,
                ),
                _SensitivityStep(
                  selected: _sensitivity,
                  healthContext: _healthContext,
                  onChanged: (s) => setState(() => _sensitivity = s),
                  onNext: _next,
                ),
                _NotificationStep(onNext: _next),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ── Step 1: Welcome ────────────────────────────────────────────────────

class _WelcomeStep extends StatelessWidget {
  const _WelcomeStep({required this.onNext});

  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: AppSpacing.allXxl,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.air, size: 64, color: AppColors.info),
          const SizedBox(height: AppSpacing.xxl),
          const Text(
            'Air Health',
            style: AppTypography.headlineLarge,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'Personal air-quality awareness.\n\n'
            'This app shows local pollution levels, short-term forecasts, '
            'and personalised alerts — so you can make informed decisions '
            'about outdoor exposure.',
            style: AppTypography.bodyLarge.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xxxxl),
          PrimaryButton(label: 'Get started', onPressed: onNext),
        ],
      ),
    );
  }
}

// ── Step 2: Location ───────────────────────────────────────────────────

class _LocationStep extends StatelessWidget {
  const _LocationStep({required this.onNext});

  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: AppSpacing.allXxl,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.location_on_outlined,
              size: 64, color: AppColors.info),
          const SizedBox(height: AppSpacing.xxl),
          const Text(
            'Your location',
            style: AppTypography.headlineLarge,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'Air quality varies by location.\n\n'
            'We use your approximate location to show nearby pollution '
            'data. Your exact coordinates are never stored or transmitted.',
            style: AppTypography.bodyLarge.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xxxxl),
          PrimaryButton(label: 'Continue', onPressed: onNext),
        ],
      ),
    );
  }
}

// ── Step 3: Health context ─────────────────────────────────────────────

class _HealthContextStep extends StatelessWidget {
  const _HealthContextStep({
    required this.selected,
    required this.onChanged,
    required this.onNext,
  });

  final UserHealthContext selected;
  final ValueChanged<UserHealthContext> onChanged;
  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SizedBox(height: AppSpacing.xxl),
          const Text('Health context', style: AppTypography.headlineLarge),
          const SizedBox(height: AppSpacing.md),
          Text(
            'This helps us suggest an appropriate alert sensitivity. '
            'It is NOT a diagnosis and stays on your device only.',
            style: AppTypography.bodyMedium.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
          ),
          const SizedBox(height: AppSpacing.xxl),
          Expanded(
            child: ListView(
              children: UserHealthContext.values.map((ctx) {
                return Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                  child: _SelectableTile(
                    label: ctx.label,
                    selected: selected == ctx,
                    onTap: () => onChanged(ctx),
                  ),
                );
              }).toList(),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.xl),
            child: PrimaryButton(label: 'Continue', onPressed: onNext),
          ),
        ],
      ),
    );
  }
}

// ── Step 4: Alert sensitivity ──────────────────────────────────────────

class _SensitivityStep extends StatelessWidget {
  const _SensitivityStep({
    required this.selected,
    required this.healthContext,
    required this.onChanged,
    required this.onNext,
  });

  final AlertSensitivity selected;
  final UserHealthContext healthContext;
  final ValueChanged<AlertSensitivity> onChanged;
  final VoidCallback onNext;

  static const _suggestSensitive = {
    UserHealthContext.asthma,
    UserHealthContext.copd,
    UserHealthContext.oncologyResp,
    UserHealthContext.otherResp,
    UserHealthContext.cardio,
  };

  @override
  Widget build(BuildContext context) {
    final suggested = _suggestSensitive.contains(healthContext)
        ? AlertSensitivity.sensitive
        : AlertSensitivity.standard;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SizedBox(height: AppSpacing.xxl),
          const Text('Alert sensitivity', style: AppTypography.headlineLarge),
          const SizedBox(height: AppSpacing.md),
          Text(
            'Choose how early you want to be notified about '
            'changing air quality.',
            style: AppTypography.bodyMedium.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
          ),
          const SizedBox(height: AppSpacing.xl),
          if (suggested != AlertSensitivity.standard) ...[
            _SuggestionBanner(
              suggested: suggested,
              onAccept: () => onChanged(suggested),
            ),
            const SizedBox(height: AppSpacing.xl),
          ],
          Expanded(
            child: ListView(
              children: AlertSensitivity.values.map((s) {
                return Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                  child: _SelectableTile(
                    label: s.label,
                    subtitle: _subtitleFor(s),
                    selected: selected == s,
                    onTap: () => onChanged(s),
                  ),
                );
              }).toList(),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.xl),
            child: PrimaryButton(label: 'Continue', onPressed: onNext),
          ),
        ],
      ),
    );
  }

  static String? _subtitleFor(AlertSensitivity s) {
    return switch (s) {
      AlertSensitivity.standard => 'Notify at Poor or worse',
      AlertSensitivity.sensitive => 'Notify at Moderately Polluted or worse',
      AlertSensitivity.high => 'Notify early, including forecast warnings',
      AlertSensitivity.custom => 'Set your own thresholds',
    };
  }
}

// ── Step 5: Notifications ──────────────────────────────────────────────

class _NotificationStep extends StatelessWidget {
  const _NotificationStep({required this.onNext});

  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: AppSpacing.allXxl,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.notifications_outlined,
              size: 64, color: AppColors.info),
          const SizedBox(height: AppSpacing.xxl),
          const Text(
            'Notifications',
            style: AppTypography.headlineLarge,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'We\'ll send you alerts when air quality changes '
            'significantly — based on your sensitivity settings.\n\n'
            'You can adjust notification preferences later in Settings.',
            style: AppTypography.bodyLarge.copyWith(
              color: AppColors.onSurfaceMuted,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xxxxl),
          PrimaryButton(label: 'Finish setup', onPressed: onNext),
        ],
      ),
    );
  }
}

// ── Shared widgets ─────────────────────────────────────────────────────

class _SelectableTile extends StatelessWidget {
  const _SelectableTile({
    required this.label,
    required this.selected,
    required this.onTap,
    this.subtitle,
  });

  final String label;
  final String? subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final borderColor =
        selected ? AppColors.info : AppColors.outlineVariant;

    return Semantics(
      button: true,
      selected: selected,
      label: label,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(AppSpacing.xl),
          decoration: BoxDecoration(
            color: selected
                ? AppColors.info.withValues(alpha: 0.06)
                : AppColors.surfaceContainer,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: borderColor, width: selected ? 1.5 : 0.5),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(label, style: AppTypography.titleMedium),
                    if (subtitle != null) ...[
                      const SizedBox(height: AppSpacing.xs),
                      Text(
                        subtitle!,
                        style: AppTypography.bodySmall.copyWith(
                          color: AppColors.onSurfaceMuted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (selected)
                const Icon(Icons.check_circle, color: AppColors.info, size: 22),
            ],
          ),
        ),
      ),
    );
  }
}

class _SuggestionBanner extends StatelessWidget {
  const _SuggestionBanner({
    required this.suggested,
    required this.onAccept,
  });

  final AlertSensitivity suggested;
  final VoidCallback onAccept;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.xl),
      decoration: BoxDecoration(
        color: AppColors.info.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: AppColors.info.withValues(alpha: 0.25),
          width: 0.5,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Based on your health context, we suggest ${suggested.label} alerts.',
            style: AppTypography.bodyMedium,
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              PrimaryButton(
                label: 'Use ${suggested.label}',
                onPressed: onAccept,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
