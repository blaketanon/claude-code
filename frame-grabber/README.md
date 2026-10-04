# Frame Grabber (web)

A browser remake of the iOS app [Frame Grabber](https://apps.apple.com/app/id1434703541): pull a full-resolution still out of any video, frame by frame, and keep the video's date and location in the saved image.

It is one self-contained page (`index.html`). There's no build step and no server. Videos are read locally and never uploaded.

## Features

| Original iOS app | This remake |
| --- | --- |
| Browse videos from the photo library | Add videos with the file picker or drag and drop. Grid shows square or fitted thumbnails with durations |
| Frame-accurate stepping | ←/→ step one frame, Shift+←/→ ten frames, Home/End. Frame rate and frame count come from the MP4/MOV sample table |
| Exact time of the current frame | `HH:MM:SS:FF` timecode or `seconds.ms`, plus frame number out of total |
| Slider with preview thumbnails | Thumbnail scrub strip with a playhead and markers for grabbed frames |
| Pull finger up to scrub slower | Slide up while dragging for ½, ¼ and ⅒ speed scrubbing |
| Export in full quality and resolution | Frames are drawn at the video's native `videoWidth × videoHeight` |
| JPEG / HEIF with compression quality | JPEG, PNG or WebP with a quality slider. Browsers can't encode HEIF |
| Keep creation date and location | Reads `mvhd`, QuickTime `keys/ilst` (iPhone), `©xyz` and 3GPP `loci`, then writes EXIF `DateTimeOriginal` (recording time + frame offset, with sub-seconds and UTC offset) and GPS into JPEG (APP1) and PNG (`eXIf`) |
| Share / save | Save one frame, save all as a `.zip`, or use the system share sheet where supported. Optional "save on grab" |
| Playback controls | Play/pause, 0.25×–2× speed |
| "Unlock All Features" in-app purchase | Everything is free |

## Run it

```sh
cd frame-grabber
python3 -m http.server 8000
# open http://localhost:8000
```

Opened from a server, it loads `sample.mp4` (a 4-second clip with a burned-in frame counter, a recording date and a location) so you can try it right away. Opened straight from `file://`, it starts empty.

On an iPhone, open the page in Safari and choose **Share → Add to Home Screen** for an app-like launch.

## Notes

- Codec support depends on the browser. HEVC (H.265) iPhone clips play in Safari, and in Chrome or Edge only where the OS provides a decoder.
- WebP exports carry no metadata. JPEG and PNG do.
- Live Photos aren't supported, since browsers can't open them as video.
