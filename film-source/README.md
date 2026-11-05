# Release video

A 30-second 1080p film at 60 fps, drawn on a canvas from real data: the RP2040 bench board's placement and routing
(`board.json`, exported from `bench/rp2040_board.py`'s routed board) and the example boards' renders (`img/`, cut from
`boards/*`). Every frame is a function of time (`window.__render(t)`). No code on screen: the hook (the name in the
board's copper), the ask (one sentence to an AI agent), three beats on the RP2040 board (placed, routed, checked),
the boards built with it, and how to start (tell your agent to install the repository).

```
cd media/release-video
node render.js keys keys/ 1.0,2.5,5.0,7.5,10.5,14,17.5,21.5   # still frames at those seconds, to check a change
node render.js all frames/                            # every frame (1812 PNGs)
ffmpeg -framerate 60 -i frames/f%05d.png -c:v libx264 -crf 17 -pix_fmt yuv420p prometheus-release.mp4
python score.py                                       # the soundtrack, synthesised (score.wav)
ffmpeg -i prometheus-release.mp4 -i score.wav -map 0:v -map 1:a -c:v copy -af loudnorm=I=-14:TP=-1.0:LRA=9 \
       -c:a aac -b:a 192k -shortest prometheus-release-sound.mp4
```

Needs Node with Playwright (Chromium) and ffmpeg. The IBM Plex fonts are embedded in `video.html` (SIL Open Font
License).

The soundtrack (`score.py`) is synthesised from nothing - no samples - and sounds on the film's own event times.
Needs numpy and scipy.
