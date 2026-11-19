# Zilcho Subs branding references

The seven PNG files in `originals/` were supplied by the owner as Copilot designs. They were copied unchanged from the main project folder at the owner's request. `sources.json` records their original names, sizes and SHA-256 hashes. These are design references, not screenshots or evidence of released features.

## App use

- `Install Icon maybe.png` is the source for `zilcho_subs/static/branding/app-logo.png`, used on sign-in and About. Claude's review revision removes the outer black corners using a cutout alpha mask while retaining source colors/text, downsizes to 512×512 and compresses the app derivative to 130,657 bytes (under 150 KB). The seven originals remain unchanged. Both HTML images declare 512×512; the visible CSS sizes remain responsive.
- `mark.svg` is an original, code-native simplification of the film strip, waveform, play and translation-bubble motif for the sidebar, favicon and queue illustration. The app name remains readable HTML text beside it.
- `branding.css` carries the navy/cyan/violet palette and responsive sign-in layout. It uses system fonts and local assets only. There is no timed splash screen, fake progress or new external request.
- The remaining supplied PNGs are kept here for future design work rather than loaded by the application. Installer Icon contains a baked-in v1.0; Splash and the banner include sample interfaces, broad language/accuracy claims and illustrated features. They are not shipping installer/store listings or actual app screenshots.
- Language/model capabilities, subtitle timing, queue behavior and account protection are unchanged. Current support depends on the installed models and translation routes, as explained beside sign-in.

No native installer or mobile/Play Store release is introduced. Do not publish the reference art as a feature guarantee. The owner may refine these assets before a public release.
