# Demo video: recording and trimming (free tools)

## Settings

- **Resolution:** 1920×1080 (1080p), 30 fps. In Windows, set Settings > System > Display > Scale to 100% or 125%,
  so the console text is readable in the video.
- **Edge:** open each console as an app window (Edge menu > Apps > Install this site as an app), or press F11 for
  full screen. Either way the address bar isn't recorded. It holds the console's access token (`?token=…`).
- **Theme:** pick one look and keep it for every scene: Classic, or HUD for scene 5's voice modes.

## Hide before you record

- **No `.env` on screen, ever**: don't open it, and close any editor tab that shows it. No terminal showing
  environment variables or keys either.
- **No API keys.** Recording needs none: both consoles run with Groq and Gemini blocked (setup.md).
- **No personal email or names in view.** That means no mail client, and no browser tabs, bookmarks bar or
  notifications. Turn on Focus / Do Not Disturb (Win+N > Do not disturb). The demo uses `example.com` addresses
  only.
- **No console link with its token.** The terminal windows print it; keep them off the recorded area, or close them
  once the consoles are open in Edge.
- **The stats panel and the Traces page** show your local history (setup.md). Stay on the chat, as in the script.
- Turn off the desktop and taskbar previews you don't want, or record one window only (below).

## Record

**Windows Game Bar (built in):**
1. Focus the Edge window and press **Win+Alt+R** to start and stop. Game Bar records one window, not the whole
   desktop.
2. Clips go to `<home>\Videos\Captures`.
3. For your voice, turn on the microphone in Win+G > Capture > the mic button. Check the level in Settings > Gaming >
   Captures.

**OBS Studio (free, obsproject.com), if you switch windows between scenes:**
1. Settings > Video: Base and Output 1920×1080, 30 fps. Settings > Output: recording format MP4, encoder "Hardware"
   if offered.
2. Sources: one "Window Capture" per console window, or a "Display Capture" with the terminal windows moved off that
   display.
3. Start Recording / Stop Recording. Files go to your Videos folder.

Record each scene as its own clip, take two if a reply comes out oddly, and do a separate take of the voice scene in
a quiet room.

## Trim and join (Clipchamp, free, built into Windows 11)

1. Open Clipchamp, create a video (16:9), and drag the clips in, in scene order.
2. **Cut the waiting.** Split at the moment you press Enter, and again just before the answer or card appears, then
   delete the middle. Or select that part and set Speed to 4–8×, and add a small "⏩ model thinking" caption so the
   speed-up is honest.
3. Add the captions from script.md, "On-screen text".
4. Keep the total near 90 s (1:30). Export at 1080p.

Before sharing, watch the export once at full screen, looking for a token, a key, a personal address, a notification,
or a number that isn't in script.md's list.
