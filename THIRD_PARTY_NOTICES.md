# Third-party notices

Echo Clip is built on **PeakCut** (https://github.com/haziqahmedqasmi/PeakCut), MIT licence. The original
copyright notice is kept in [LICENSE](LICENSE).

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
