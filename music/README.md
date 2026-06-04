# Music beds

Drop **royalty-free instrumental** tracks here and the pipeline mixes one under the
voiceover automatically (ducked so the narration stays clear). No track = voiceover-only,
never an error.

## How it picks a track

`config.MUSIC_BY_SCHEME` maps each colour scheme to a **mood**, and the mixer looks for
`<mood>.<ext>` in this folder, then `default.<ext>`. Supported: `.mp3 .m4a .wav .ogg .flac .opus`.

| Scheme | Mood → filename | Use it for |
|---|---|---|
| dark_red | `tense` | scandal / fraud (Theranos, FTX) |
| dark_gold | `news` | urgent / breaking (Ever Given) |
| dark_blue | `epic` | survival / triumph (Apollo 13) |
| dark_purple | `dramatic` | sport / iconic drama (Maradona) |
| dark_green | `uplifting` | positive turns |
| *(any, fallback)* | `default` | used when the mood file is missing |

So a minimum viable setup is a single `default.mp3`. A tailored setup is
`tense.mp3`, `news.mp3`, `epic.mp3`, `dramatic.mp3`, `uplifting.mp3`.

## Where to get them (truly free, no attribution needed)

1. **Pixabay Music** — https://pixabay.com/music/ — free for commercial use, **no attribution**.
2. **YouTube Audio Library** — studio.youtube.com → Audio Library — filter to "No attribution".
3. **Free Music Archive** / **Incompetech (Kevin MacLeod)** — free but most require **attribution**
   (fine for some platforms; check the licence).

Search terms that match each mood:

- **tense** → "dark tension", "suspense underscore", "ominous documentary", "investigation"
- **news** → "breaking news", "urgent news bed", "ticking pulse", "broadcast underscore"
- **epic** → "cinematic build", "inspiring orchestral", "hopeful epic", "triumphant"
- **dramatic** → "dramatic orchestral", "epic sport", "intense trailer"
- **uplifting** → "uplifting corporate", "positive inspiring"

## Picking good tracks

- **Instrumental only** (no vocals — they fight the voiceover).
- ~30–60s is plenty; short tracks are **looped automatically** to fill the video.
- Loudness/level is handled for you (ducked ~9 dB under the VO, whole mix normalised to
  -14 LUFS), so don't worry about matching volumes — just pick the vibe.
- Avoid tracks with a huge quiet intro; the bed should establish quickly.

## Tuning (in `config.py`)

- `MUSIC_BED_GAIN_DB` (default -9): lower = quieter bed.
- `MUSIC_DUCK_RATIO` (default 8): higher = music drops further while the VO speaks.
- `MUSIC_FADE` (default 1.0s): fade in/out length.
- `MUSIC_ENABLED = False`: turn the whole feature off.
