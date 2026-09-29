import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../domain/models/models.dart';
import '../../notifications/notification_service.dart';
import '../../providers/alert_providers.dart';
import '../../providers/location_providers.dart';
import '../../providers/onboarding_providers.dart';
import '../../providers/prefs_providers.dart';
import '../../providers/profile_providers.dart';
import '../../services/location_service.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';

/// Multi-step onboarding flow.
///
/// Steps:
/// 1. Welcome
/// 2. Location permission explanation
/// 3. Health context (optional)
/// 4. Alert sensitivity
/// 5. Notification permission
///
/// Completing step 5 finishes onboarding and routes to Home.
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  final _pageController = PageController();
  int _currentPage = 0;

  UserHealthContext _healthContext = UserHealthContext.none;
  DiseaseSeverity _diseaseSeverity = DiseaseSeverity.moderate;
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
      diseaseSeverity: _healthContext != UserHealthContext.none &&
              _healthContext != UserHealthContext.preferNotToSay
          ? _diseaseSeverity
          : null,
    );
    await ref.read(userProfileProvider.notifier).updateProfile(profile);
    await ref.read(prefsStoreProvider).setOnboardingDone(true);
    // Invalidate so the router redirect sees the updated onboarding state.
    ref.invalidate(onboardingDoneProvider);
    ref.invalidate(onboardingCompleteProvider);
    if (mounted) context.go('/home');
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

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
                  color: cs.onSurfaceVariant,
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
                  severity: _diseaseSeverity,
                  onChanged: (ctx) => setState(() => _healthContext = ctx),
                  onSeverityChanged: (sev) => setState(() => _diseaseSeverity = sev),
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

// ── Continue button (bottom-right, FilledButton, clears dev toolbar) ───

class _ContinueButton extends StatelessWidget {
  const _ContinueButton({required this.label, required this.onNext});

  final String label;
  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 48, right: AppSpacing.xl),
      child: Align(
        alignment: Alignment.bottomRight,
        child: FilledButton(
          onPressed: onNext,
          child: Text(label),
        ),
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
    final cs = Theme.of(context).colorScheme;

    return Padding(
      padding: AppSpacing.allXxl,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.air, size: 64, color: cs.primary),
          const SizedBox(height: AppSpacing.xxl),
          Text(
            'Air Health',
            style: AppTypography.headlineLarge.copyWith(
              color: cs.onSurface,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'Personal air-quality awareness.\n\n'
            'This app shows local pollution levels, short-term forecasts, '
            'and personalised alerts — so you can make informed decisions '
            'about outdoor exposure.',
            style: AppTypography.bodyLarge.copyWith(
              color: cs.onSurfaceVariant,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xxxxl),
          _ContinueButton(label: 'Get started', onNext: onNext),
        ],
      ),
    );
  }
}

// ── Step 2: Location ───────────────────────────────────────────────────

class _LocationStep extends ConsumerStatefulWidget {
  const _LocationStep({required this.onNext});

  final VoidCallback onNext;

  @override
  ConsumerState<_LocationStep> createState() => _LocationStepState();
}

class _LocationStepState extends ConsumerState<_LocationStep> {
  String? _status;

  Future<void> _requestLocation() async {
    final result = await ref.read(locationServiceProvider).requestAndLocate();
    if (!mounted) return;

    final message = switch (result) {
      LocationSuccess() =>
        'Location enabled — nearby data will use your position.',
      LocationDenied() =>
        'Permission denied. You can enable it later in Settings.',
      LocationPermanentlyDenied() =>
        'Location is blocked. Enable it in system settings.',
      LocationUnavailable() =>
        'Location unavailable right now — try again later.',
    };
    setState(() => _status = message);

    if (result is LocationSuccess) {
      // Re-resolve so the rest of the app uses the real position.
      ref.invalidate(currentLocationProvider);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Padding(
      padding: AppSpacing.allXxl,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.location_on_outlined, size: 64, color: cs.primary),
          const SizedBox(height: AppSpacing.xxl),
          Text(
            'Your location',
            style: AppTypography.headlineLarge.copyWith(color: cs.onSurface),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'Air quality varies by location.\n\n'
            'We use your approximate location to show nearby pollution '
            'data. Your exact coordinates are never stored or transmitted.',
            style: AppTypography.bodyLarge.copyWith(
              color: cs.onSurfaceVariant,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xl),
          FilledButton.tonalIcon(
            onPressed: _requestLocation,
            icon: const Icon(Icons.my_location),
            label: const Text('Allow location'),
          ),
          if (_status != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              _status!,
              style: AppTypography.bodySmall.copyWith(
                color: cs.onSurfaceVariant,
              ),
              textAlign: TextAlign.center,
            ),
          ],
          const SizedBox(height: AppSpacing.xl),
          _ContinueButton(label: 'Continue', onNext: widget.onNext),
        ],
      ),
    );
  }
}

// ── Step 3: Health context ─────────────────────────────────────────────

class _HealthContextStep extends StatelessWidget {
  const _HealthContextStep({
    required this.selected,
    required this.severity,
    required this.onChanged,
    required this.onSeverityChanged,
    required this.onNext,
  });

  final UserHealthContext selected;
  final DiseaseSeverity severity;
  final ValueChanged<UserHealthContext> onChanged;
  final ValueChanged<DiseaseSeverity> onSeverityChanged;
  final VoidCallback onNext;

  bool get _isPatient =>
      selected != UserHealthContext.none &&
      selected != UserHealthContext.preferNotToSay;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: AppSpacing.xxl),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          child: Text('Health context',
              style: AppTypography.headlineLarge.copyWith(
                color: cs.onSurface,
              )),
        ),
        const SizedBox(height: AppSpacing.md),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          child: Text(
            'This helps us suggest an appropriate alert sensitivity. '
            'It is NOT a diagnosis and stays on your device only.',
            style: AppTypography.bodyMedium.copyWith(
              color: cs.onSurfaceVariant,
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.xxl),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
            children: [
              ...UserHealthContext.values.map((ctx) {
                return Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                  child: _SelectableTile(
                    label: ctx.label,
                    selected: selected == ctx,
                    onTap: () => onChanged(ctx),
                  ),
                );
              }),
              if (_isPatient) ...[
                const SizedBox(height: AppSpacing.lg),
                Text(
                  'Condition Severity',
                  style: AppTypography.titleMedium.copyWith(color: cs.onSurface),
                ),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  'Controls rising AQI alert thresholds and 5-sec warning sounds.',
                  style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant),
                ),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.sm,
                  children: DiseaseSeverity.values.map((s) {
                    final isSel = s == severity;
                    return ChoiceChip(
                      label: Text(s.label),
                      selected: isSel,
                      onSelected: (_) => onSeverityChanged(s),
                    );
                  }).toList(),
                ),
                const SizedBox(height: AppSpacing.md),
              ],
            ],
          ),
        ),
        _ContinueButton(label: 'Continue', onNext: onNext),
      ],
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
    final cs = Theme.of(context).colorScheme;
    final suggested = _suggestSensitive.contains(healthContext)
        ? AlertSensitivity.sensitive
        : AlertSensitivity.standard;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: AppSpacing.xxl),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          child: Text('Alert sensitivity',
              style: AppTypography.headlineLarge.copyWith(
                color: cs.onSurface,
              )),
        ),
        const SizedBox(height: AppSpacing.md),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          child: Text(
            'Choose how early you want to be notified about '
            'changing air quality.',
            style: AppTypography.bodyMedium.copyWith(
              color: cs.onSurfaceVariant,
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.xl),
        if (suggested != AlertSensitivity.standard) ...[
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
            child: _SuggestionBanner(
              suggested: suggested,
              onAccept: () => onChanged(suggested),
            ),
          ),
          const SizedBox(height: AppSpacing.xl),
        ],
        Expanded(
          child: ListView(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
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
        _ContinueButton(label: 'Continue', onNext: onNext),
      ],
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

class _NotificationStep extends ConsumerStatefulWidget {
  const _NotificationStep({required this.onNext});

  final VoidCallback onNext;

  @override
  ConsumerState<_NotificationStep> createState() => _NotificationStepState();
}

class _NotificationStepState extends ConsumerState<_NotificationStep> {
  String? _status;

  Future<void> _requestNotifications() async {
    final result =
        await ref.read(notificationServiceProvider).requestPermissions();
    if (!mounted) return;

    final message = switch (result) {
      NotificationPermissionResult.granted => 'Notifications enabled.',
      NotificationPermissionResult.denied =>
        'Not enabled. You can turn notifications on later in Settings.',
      NotificationPermissionResult.permanentlyDenied =>
        'Notifications are blocked. Enable them in system settings.',
    };
    setState(() => _status = message);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Padding(
      padding: AppSpacing.allXxl,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.notifications_outlined, size: 64, color: cs.primary),
          const SizedBox(height: AppSpacing.xxl),
          Text(
            'Notifications',
            style: AppTypography.headlineLarge.copyWith(color: cs.onSurface),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'We\'ll send you alerts when air quality changes '
            'significantly — based on your sensitivity settings.\n\n'
            'You can adjust notification preferences later in Settings.',
            style: AppTypography.bodyLarge.copyWith(
              color: cs.onSurfaceVariant,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xl),
          FilledButton.tonalIcon(
            onPressed: _requestNotifications,
            icon: const Icon(Icons.notifications_active_outlined),
            label: const Text('Enable notifications'),
          ),
          if (_status != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              _status!,
              style: AppTypography.bodySmall.copyWith(
                color: cs.onSurfaceVariant,
              ),
              textAlign: TextAlign.center,
            ),
          ],
          const SizedBox(height: AppSpacing.xl),
          _ContinueButton(label: 'Finish setup', onNext: widget.onNext),
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
    final cs = Theme.of(context).colorScheme;
    final borderColor = selected ? cs.primary : cs.outlineVariant;

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
                ? cs.primary.withValues(alpha: 0.08)
                : cs.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: borderColor, width: selected ? 1.5 : 0.5),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      style: AppTypography.titleMedium.copyWith(
                        color: cs.onSurface,
                      ),
                    ),
                    if (subtitle != null) ...[
                      const SizedBox(height: AppSpacing.xs),
                      Text(
                        subtitle!,
                        style: AppTypography.bodySmall.copyWith(
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (selected)
                Icon(Icons.check_circle, color: cs.primary, size: 22),
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
    final cs = Theme.of(context).colorScheme;

    return Container(
      padding: const EdgeInsets.all(AppSpacing.xl),
      decoration: BoxDecoration(
        color: cs.primaryContainer.withValues(alpha: 0.4),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: cs.primary.withValues(alpha: 0.25),
          width: 0.5,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Based on your health context, we suggest ${suggested.label} alerts.',
            style: AppTypography.bodyMedium.copyWith(color: cs.onSurface),
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              FilledButton(
                onPressed: onAccept,
                child: Text('Use ${suggested.label}'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
