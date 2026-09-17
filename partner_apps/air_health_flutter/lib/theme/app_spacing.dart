import 'package:flutter/material.dart';

/// 8pt-grid spacing scale. Every dimension in the app comes from here.
abstract final class AppSpacing {
  static const double xs = 2;
  static const double sm = 4;
  static const double md = 8;
  static const double lg = 12;
  static const double xl = 16;
  static const double xxl = 24;
  static const double xxxl = 32;
  static const double xxxxl = 48;
  static const double xxxxxl = 64;

  // Convenience EdgeInsets
  static const EdgeInsets allSm = EdgeInsets.all(sm);
  static const EdgeInsets allMd = EdgeInsets.all(md);
  static const EdgeInsets allLg = EdgeInsets.all(lg);
  static const EdgeInsets allXl = EdgeInsets.all(xl);
  static const EdgeInsets allXxl = EdgeInsets.all(xxl);

  static const EdgeInsets horizontalXl = EdgeInsets.symmetric(horizontal: xl);
  static const EdgeInsets horizontalXxl = EdgeInsets.symmetric(horizontal: xxl);

  static const EdgeInsets verticalMd = EdgeInsets.symmetric(vertical: md);
  static const EdgeInsets verticalLg = EdgeInsets.symmetric(vertical: lg);

  static const EdgeInsets pagePadding = EdgeInsets.symmetric(
    horizontal: xl,
    vertical: lg,
  );

  // Gaps for use in Column/Row children.
  static const SizedBox gapHxs = SizedBox(height: xs);
  static const SizedBox gapHsm = SizedBox(height: sm);
  static const SizedBox gapHmd = SizedBox(height: md);
  static const SizedBox gapHlg = SizedBox(height: lg);
  static const SizedBox gapHxl = SizedBox(height: xl);
  static const SizedBox gapHxxl = SizedBox(height: xxl);

  static const SizedBox gapWsm = SizedBox(width: sm);
  static const SizedBox gapWmd = SizedBox(width: md);
  static const SizedBox gapWlg = SizedBox(width: lg);
  static const SizedBox gapWxl = SizedBox(width: xl);
}
