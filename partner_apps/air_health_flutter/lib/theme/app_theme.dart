import 'package:flutter/material.dart';

import 'tokens.dart';

/// Material 3 theme — neutral surfaces, restrained styling.
/// AQI color is the only hue doing real work (applied per-component,
/// not baked into the theme).
abstract final class AppTheme {
  static ThemeData get light {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF455A64), // blue-grey — neutral anchor
      brightness: Brightness.light,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: colorScheme,
      scaffoldBackgroundColor: colorScheme.surface,
      appBarTheme: AppBarTheme(
        centerTitle: false,
        elevation: Tokens.elevNone,
        backgroundColor: colorScheme.surface,
        foregroundColor: colorScheme.onSurface,
      ),
      cardTheme: CardThemeData(
        elevation: Tokens.elevSm,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Tokens.radiusMd),
        ),
        margin: const EdgeInsets.symmetric(
          horizontal: Tokens.sp16,
          vertical: Tokens.sp8,
        ),
      ),
      navigationBarTheme: const NavigationBarThemeData(
        elevation: Tokens.elevSm,
        height: 64,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      ),
      inputDecorationTheme: InputDecorationTheme(
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Tokens.radiusSm),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: Tokens.sp16,
          vertical: Tokens.sp12,
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Tokens.radiusSm),
          ),
          padding: const EdgeInsets.symmetric(
            horizontal: Tokens.sp24,
            vertical: Tokens.sp12,
          ),
        ),
      ),
    );
  }

  static ThemeData get dark {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF455A64),
      brightness: Brightness.dark,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: colorScheme,
      scaffoldBackgroundColor: colorScheme.surface,
      appBarTheme: AppBarTheme(
        centerTitle: false,
        elevation: Tokens.elevNone,
        backgroundColor: colorScheme.surface,
        foregroundColor: colorScheme.onSurface,
      ),
      cardTheme: CardThemeData(
        elevation: Tokens.elevSm,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Tokens.radiusMd),
        ),
        margin: const EdgeInsets.symmetric(
          horizontal: Tokens.sp16,
          vertical: Tokens.sp8,
        ),
      ),
      navigationBarTheme: const NavigationBarThemeData(
        elevation: Tokens.elevSm,
        height: 64,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      ),
    );
  }
}
