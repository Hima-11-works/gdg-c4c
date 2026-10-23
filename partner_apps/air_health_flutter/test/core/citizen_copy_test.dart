import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/core/citizen_copy.dart';

void main() {
  test('Hindi and Punjabi translate the report controls and key choices', () {
    const hindi = CitizenCopy('hi');
    const punjabi = CitizenCopy('pa');

    expect(hindi.text('where'), 'यह कहाँ हो रहा है?');
    expect(hindi.fireKind('industrial_fire'), 'कारखाने में आग');
    expect(hindi.smoke(3), 'ज़्यादा');
    expect(punjabi.text('where'), 'ਇਹ ਕਿੱਥੇ ਹੋ ਰਿਹਾ ਹੈ?');
    expect(punjabi.fireKind('crop_burning'), 'ਲੱਕੜ / ਫਸਲ ਸਾੜਨਾ');
    expect(punjabi.text('voice_privacy'), contains('ਆਡੀਓ'));
  });

  test('unknown translations fall back to English', () {
    expect(const CitizenCopy('xx').text('submit'), 'Submit report');
    expect(const CitizenCopy('hi').text('submit'), 'रिपोर्ट भेजें');
  });
}
