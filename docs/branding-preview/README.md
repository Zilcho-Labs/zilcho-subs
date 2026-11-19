# Branding previews (0.2.1)

These are screenshots of the actual locally running application with synthetic queue state. No user media, account details or inference output were used. The sample running job is a fixture, not a speech-processing result. The local preview reports FFmpeg missing because no native inference installation was required for UI review.

- [Desktop sign-in](desktop-signin.png)
- [Desktop queue](desktop-queue.png)
- [Phone sign-in](phone-signin.png)
- [Phone queue](phone-queue.png)
- [Desktop About](desktop-about.png)
- [Phone About](phone-about.png)

Reviewed with Chromium at 1440x1000 and 390x844. All seven views were also checked at 320px width for horizontal overflow. Login, navigation and dynamic 0.2.1 footer worked with no JavaScript errors or external page requests. This is not a real-device or inference acceptance test.

Refreshed after Claude review: sign-in/About now load the 512×512 transparent-corner logo (130,657 bytes). The original PNG design references are unchanged.
