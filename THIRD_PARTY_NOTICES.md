# Third-party notices

## PeakCut

Echo Clip includes code from **PeakCut** (https://github.com/haziqahmedqasmi/PeakCut), used under the MIT licence,
whose notice is reproduced here as that licence requires:

```
MIT License

Copyright (c) 2026 PeakCut contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Shipped in this repository

| What | Where | Licence |
|---|---|---|
| Anton, Archivo Black, Poppins (SemiBold, Bold, ExtraBold, Black), Bebas Neue, Bangers caption fonts | `studio/fonts/` | SIL Open Font License 1.1 (texts: `studio/fonts/OFL-*.txt`) |
| YuNet face detection model (`face_detection_yunet_2023mar.onnx`), from the OpenCV Zoo | `studio/models/` | MIT (text: `studio/models/LICENSE-yunet.txt`) |
| Noto Sans Devanagari (variable, weights 100–900), for Hindi captions and headlines | `studio/fonts/NotoSansDevanagari-Variable.ttf` | SIL Open Font License 1.1 (text: `studio/fonts/OFL-notosansdevanagari.txt`) |
| Built-in music tracks (Arcade Rush, Goofy Bounce, Night Tension, Lo-fi Drift, Big Moment) | made at first start into `data/music/` by `studio/music.py` | Original, composed by Echo Clip's code: free to use |

## Downloaded or installed, not shipped

| What | Licence |
|---|---|
| NVIDIA Parakeet TDT 0.6B v2 speech model (sherpa-onnx int8 export), fetched once into `data/models/` | CC-BY-4.0 |
| sherpa-onnx | Apache-2.0 |
| OpenAI Whisper large-v3-turbo (CTranslate2 conversion), fetched once into `data/models/whisper/` for Hindi and other languages | MIT |
| faster-whisper, CTranslate2 | MIT |
| PyAV (installed with faster-whisper) | BSD-3-Clause |
| FriBiDi (installed with Homebrew for Hindi text shaping; not shipped) | LGPL-2.1 |
| OpenCV (`opencv-python-headless`) | Apache-2.0 |
| FFmpeg (installed separately, e.g. Homebrew) | LGPL/GPL, depending on the build |
| yt-dlp | Unlicense |
| FastAPI, Uvicorn, httpx, NumPy, Pillow, SQLAlchemy and the other Python packages in `requirements.txt` | their own permissive licences |

Echo Clip is not affiliated with, endorsed by or connected to Rockstar Games, Take-Two Interactive, YouTube,
Google, Twitch, Kick, OpusClip, Featherless AI, Anthropic, or any streamer. Game and platform names are used
only to describe what the software works with.
