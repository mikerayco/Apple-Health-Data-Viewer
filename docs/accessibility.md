# Accessibility verification

The interface targets WCAG 2.2 AA. Automated tests currently verify semantic page rendering, accessible error shells, self-contained assets, security headers, and bounded data responses; they do not replace browser and assistive-technology review.

## Manual release pass

Test light, dark, and system themes with synthetic data.

- Navigate every page using Tab, Shift+Tab, Enter, Space, arrow keys where expected, and Escape for the compact menu.
- Confirm the skip link reaches `main`, focus is always visible, focus order follows the visual order, and no control traps focus.
- Verify one `h1` per page, logical heading order, landmarks, current-page state, form labels/legends, validation messages, and status announcements.
- Check date presets and custom dates, unit/theme choices, upload controls, workout filters, pagination, destructive confirmations, route detail, and ECG detail.
- At 200% browser zoom and widths down to 320 CSS pixels, confirm content reflows without two-dimensional page scrolling except bounded data visualizations.
- Verify text and meaningful UI component contrast with a WCAG contrast tool. Do not infer pass/fail from appearance alone.
- Enable reduced motion and confirm transitions are removed or nonessential.
- With a screen reader, complete setup navigation, inspect one metric summary/table, open one workout and ECG, and remove synthetic processed data.
- Confirm chart, route, and waveform meaning is available through adjacent text, metadata, summaries, or tables; canvas/SVG alone must not be the only representation.
- Trigger empty, sparse, unsupported, malformed-import, and not-found states and confirm each explains recovery without exposing private details.

Record browser, operating system, assistive technology, audit tool/version, failures, fixes, and accepted exceptions in the release notes. Do not record screenshots or output containing private health data.
