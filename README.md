# insta360-link-2

Browser-based viewer and gimbal controller for the [Insta360 Link 2](https://www.insta360.com/product/insta360-link2) running off a Raspberry Pi. No vendor software, no kernel modules, no reverse-engineered HID — the Link 2 already exposes pan / tilt / zoom as standard UVC controls, and the whole thing is one Python file using only the stdlib.

Click-and-drag the live video to aim the gimbal. Scroll wheel to zoom. Works in any browser or VLC.

## How it works

- `ffmpeg` reads MJPEG frames directly from `/dev/video0` (no re-encode; the Link 2 emits MJPEG natively).
- A tiny `http.server` threads connected clients and multiplexes frames to all of them as `multipart/x-mixed-replace`. Multiple simultaneous viewers are fine.
- A `/ptz` endpoint pokes `v4l2-ctl -c pan_absolute=...,tilt_absolute=...,zoom_absolute=...` — these are the standard UVC Camera Controls the Link 2 already exposes.
- `ffmpeg` is auto-restarted if the USB device drops or re-enumerates.

## Requirements

- Raspberry Pi (tested on Pi 4/5) running Raspberry Pi OS
- Insta360 Link 2 plugged into a **USB 3 port** (the blue one). USB 2 will enumerate the camera at high-speed and drop frames under load.
- `ffmpeg`, `v4l-utils`, `python3` (all preinstalled on Raspberry Pi OS Desktop; on Lite: `sudo apt install ffmpeg v4l-utils`)

## Run

```bash
python3 stream.py
```

Then open `http://<pi-ip>:8090/` from any device on the same network.

To keep it running after you disconnect:

```bash
nohup python3 stream.py > stream.log 2>&1 &
```

## Controls

| Input | Action |
|---|---|
| Drag video | Pan / tilt (at 1× zoom, a full-width drag ≈ a full FOV sweep) |
| Scroll wheel | Zoom in / out (1×–4× digital) |
| `center` button | Return to pan=0, tilt=0, zoom=1× |

Sensitivity auto-scales inversely with zoom, so framing gets finer as you zoom in.

## Endpoints

- `GET /` — HTML viewer
- `GET /stream` — live MJPEG as `multipart/x-mixed-replace` (openable in VLC, browsers, `<img>` tags, OpenCV, etc.)
- `GET /ptz?pan=<int>&tilt=<int>&zoom=<int>` — set absolute PTZ state, returns JSON. All three params optional.

PTZ units are in arcseconds (UVC convention): `3600` = 1°. Ranges:

- `pan_absolute`: ±522000 (≈ ±145°)
- `tilt_absolute`: −324000 to +360000 (≈ −90° to +100°)
- `zoom_absolute`: 100–400 (1.0×–4.0×)

## Notes

- Tuned for 1280×720 @ 30 fps to stay comfortably within USB bandwidth. Edit `FFMPEG` in `stream.py` to crank it (`1920×1080 @ 60`, `3840×2160 @ 30` are supported by the camera).
- The Link 2's "AI tracking", "gesture control", and "preset" features are implemented in Insta360's Windows/macOS app and are not exposed via UVC — this project doesn't touch them.
