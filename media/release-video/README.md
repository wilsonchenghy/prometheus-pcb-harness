# Release video

A 67-second 1080p film drawn on a canvas from real data: the RP2040 bench board's placement and routing
(`board.json`, exported from `bench/rp2040_board.py`'s routed board) and the example boards' renders
(`img/`, cut from `boards/*`). Every frame is a function of time (`window.__render(t)`).

```
cd media/release-video
node render.js keys keys/ 3.6,20.5,43.5,52.6,61.6     # still frames at those seconds, to check a change
node render.js all frames/                            # every frame (2010 PNGs)
ffmpeg -framerate 30 -i frames/f%05d.png -c:v libx264 -crf 17 -pix_fmt yuv420p pcb-harness-release.mp4
```

Needs Node with Playwright (Chromium) and ffmpeg. The IBM Plex fonts are embedded in `video.html` (SIL Open Font
License).
