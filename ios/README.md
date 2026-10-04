# Stillcut for iPhone and iPad

A native SwiftUI remake of [Frame Grabber](https://apps.apple.com/app/id1434703541). Pick a video or Live Photo, find the exact frame, and save it as a full-resolution photo that keeps the video's date and location.

Requires Xcode 16 or later. Runs on iOS 17 and later.

## Run it

1. Open `ios/Stillcut.xcodeproj` in Xcode.
2. Select the **Stillcut** target › **Signing & Capabilities**, choose your Team, and change the bundle identifier from `com.example.stillcut` to one you own.
3. Pick your iPhone (or a simulator) and press **Run**.

The simulator's photo library has no videos. Drag a video onto the simulator window to add one, or use the folder button to open one from Files.

## Features

- **Library:** your videos and Live Photos, newest first. Filter by type, switch between square and fitted thumbnails, or open a video from Files.
- **Frame-exact navigation:** step ±1 or ±10 frames (hold to repeat). On iPad with a keyboard, use ← → (with Shift for 10), Space to play, and G to grab.
- **Scrub strip:** thumbnails with a playhead and markers for frames you've grabbed. Slide your finger up while dragging for ½, ¼ and ⅒ speed.
- **Readout:** `HH:MM:SS:FF` timecode or `MM:SS.mmm`, frame N of total, frame rate and resolution.
- **Grab:** decodes the exact frame at the video's full resolution, upright, with zero time tolerance.
- **Export:** HEIF, JPEG or PNG, with a quality slider.
- **Metadata:** `DateTimeOriginal` is the recording time plus the frame's offset, with sub-seconds and the recording's UTC offset. GPS comes from Photos or the file's QuickTime metadata. Saved Photos entries get the same date and place.
- **Save and share:** save one frame or all of them to Photos, share through the share sheet, or preview a frame full screen.
- **Playback:** ¼×, ½×, 1× and 2× speeds.
- Everything stays on the device.

## Project layout

```
Stillcut/
  App/       App entry, settings screen, shared colors
  Library/   Photo library grid and PhotoKit fetching
  Editor/    Player, scrub strip, frame tray, video details
  Export/    Video loading and metadata, image encoding, saving to Photos
  Support/   Preferences, timecode, ISO 6709 and QuickTime date parsing
```

The project uses Xcode 16 synchronized folders, so new files added under `Stillcut/` are picked up automatically.

## Renaming

The working name lives in three places: `AppInfo.name` in `App/StillcutApp.swift`, `INFOPLIST_KEY_CFBundleDisplayName`, and the two photo-library permission strings in the target's build settings.

### Name ideas

| Name | Why it works |
| --- | --- |
| **Stillcut** | A still image pulled with an editor's cut. Short, and reads well under an icon |
| **Stillpoint** | From T. S. Eliot's "the still point of the turning world": the one frame where motion stops |
| **One Twenty-Fourth** (1/24) | One frame of cinema. Distinctive for film nerds |
| **Sprocket** | The holes that pull film through a camera. Warm and mechanical |
| **Framejack** | Steal a frame. Playful and a little punk |
| **Pluck** | Pluck a moment out of a clip. A friendly verb |
| **Freezeframe** | Instantly clear, though likely crowded on the App Store |
| **Midframe** | The moment you meant to catch, between the ones you got |
| **Halt** | A one-word app with a stop-motion feel |
| **Keepframe** | Plays on *keyframe*. It's the frame you keep |
