"""
Free English podcast generator (no paid APIs).

Lessons come from lessons.json, audio from edge-tts (free), mixing via pydub/ffmpeg.

Usage:
  python generate_episode.py                    # next lesson -> episode + feed + git push
  python generate_episode.py --no-push          # same, but do not commit/push
  python generate_episode.py --preview ID       # build preview/ID.mp3 only (no feed, no git)
"""
import argparse
import asyncio
import hashlib
import json
import os
import random
import shutil
import subprocess
import time
from datetime import date
from email.utils import formatdate
from pathlib import Path
from xml.sax.saxutils import escape
from dotenv import load_dotenv

import edge_tts
from pydub import AudioSegment

# Always run from the script's directory
os.chdir(Path(__file__).parent)
load_dotenv()

# ffmpeg: use the one on PATH; otherwise FFMPEG_BIN from .env (folder containing ffmpeg.exe)
if not shutil.which("ffmpeg"):
    ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    if ffmpeg_bin:
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found. Install it (winget install ffmpeg) or set FFMPEG_BIN in .env")

# Voices (all free, Microsoft Edge neural voices)
VOICE_AR   = "ar-SA-ZariyahNeural"   # Arabic narrator - Saudi female
VOICE_EN_F = "en-US-AriaNeural"      # English - US female (main English voice)
VOICE_EN_M = "en-US-GuyNeural"       # English - US male (second speaker in dialogues)
SLOW_RATE  = "-30%"                  # slow version of English sentences

BASE_URL = "https://m509r2.github.io/english-podcast"

LESSONS_FILE = Path("lessons.json")
USED_FILE    = Path("used_lessons.txt")   # ids already published, one per line

# All Arabic narration is fully diacritized (tashkeel): without it the Arabic voice
# stretches word endings ("النادللل"). Keep new Arabic text diacritized too.
ORDINALS_AR = ["الْأُولَى", "الثَّانِيَةُ", "الثَّالِثَةُ", "الرَّابِعَةُ", "الْخَامِسَةُ", "السَّادِسَةُ", "السَّابِعَةُ", "الثَّامِنَةُ"]

INTROS = [
    "أَهْلًا بِكَ فِي حَلْقَةٍ جَدِيدَةٍ مِنْ تَعَلُّمِ الْإِنْجِلِيزِيَّةِ يَوْمِيًّا. مَوْضُوعُنَا الْيَوْمَ: {topic}.",
    "مَرْحَبًا بِكَ يَا صَدِيقِي فِي حَلْقَةٍ جَدِيدَةٍ. دَرْسُنَا الْيَوْمَ: {topic}.",
    "أَهْلًا وَسَهْلًا بِكَ. فِي هَذِهِ الْحَلْقَةِ سَنَتَعَلَّمُ مَعًا: {topic}.",
]
HOW_TO_AR = "اسْتَمِعْ جَيِّدًا، وَعِنْدَمَا تَسْمَعُ الصَّمْتَ، كَرِّرِ الْجُمْلَةَ بِصَوْتٍ عَالٍ. التَّكْرَارُ هُوَ سِرُّ الْإِتْقَانِ."
CLOSINGS = [
    "تَدَرَّبْ عَلَى جُمَلِ الْيَوْمِ، وَنَلْقَاكَ غَدًا بِإِذْنِ اللَّهِ.",
    "اسْتَمِعْ لِهَذِهِ الْحَلْقَةِ مَرَّةً ثَانِيَةً إِنِ اسْتَطَعْتَ، وَإِلَى اللِّقَاءِ غَدًا.",
    "الْمُمَارَسَةُ الْيَوْمِيَّةُ هِيَ سِرُّ التَّعَلُّمِ. أَرَاكَ غَدًا.",
]
AR_WORDS    = "أَوَّلًا: الْكَلِمَاتُ الْمُهِمَّةُ."
AR_SENTS    = "ثَانِيًا: جُمَلُ الْيَوْمِ."
AR_DIALOGUE = "ثَالِثًا: الْحِوَارُ."
AR_QUIZ     = "رَابِعًا: اخْتَبِرْ نَفْسَكَ. سَأَسْأَلُكَ، وَحَاوِلْ أَنْ تُجِيبَ قَبْلَ أَنْ تَسْمَعَ الْجَوَابَ."
AR_REVIEW   = "وَأَخِيرًا، لِنُرَاجِعْ جُمَلَ الْيَوْمِ بِسُرْعَةٍ."
AR_SENTENCE = "الْجُمْلَةُ"
AR_MEANING  = "الْمَعْنَى:"
AR_ANSWER   = "الْجَوَابُ:"
AR_NEXT     = "فِي الْحَلْقَةِ الْقَادِمَةِ سَنَتَعَلَّمُ: {topic}."


# ── Lessons ───────────────────────────────────────────────────────────────────
def load_lessons() -> list:
    return json.loads(LESSONS_FILE.read_text(encoding="utf-8"))


def used_ids() -> list:
    return USED_FILE.read_text(encoding="utf-8").split() if USED_FILE.exists() else []


def pick_lesson(lesson_id: str | None = None) -> tuple[dict, dict | None]:
    """Return (lesson, following_lesson_or_None)."""
    lessons = load_lessons()
    if lesson_id:
        idx = next((i for i, l in enumerate(lessons) if l["id"] == lesson_id), None)
        if idx is None:
            raise SystemExit(f"Lesson '{lesson_id}' not found in lessons.json")
        following = lessons[idx + 1] if idx + 1 < len(lessons) else None
        return lessons[idx], following

    used = set(used_ids())
    remaining = [l for l in lessons if l["id"] not in used]
    if not remaining:
        raise SystemExit("All lessons in lessons.json are used. Add new lessons to continue.")
    following = remaining[1] if len(remaining) > 1 else None
    return remaining[0], following


def mark_used(lesson_id: str):
    with USED_FILE.open("a", encoding="utf-8") as f:
        f.write(lesson_id + "\n")


# ── TTS (with cache + retries) ────────────────────────────────────────────────
async def _tts(text: str, voice: str, rate: str, path: Path):
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


class Speaker:
    def __init__(self, tmp_dir: Path):
        self.tmp_dir = tmp_dir
        self.cache: dict[str, AudioSegment] = {}

    def say(self, text: str, voice: str, rate: str = "+0%") -> AudioSegment:
        key = hashlib.md5(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()
        if key in self.cache:
            return self.cache[key]
        path = self.tmp_dir / f"{key}.mp3"
        for attempt in range(4):
            try:
                asyncio.run(_tts(text, voice, rate, path))
                break
            except Exception as e:  # network hiccups
                if attempt == 3:
                    raise
                print(f"  TTS retry ({e})")
                time.sleep(2 * (attempt + 1))
        seg = AudioSegment.from_file(str(path), format="mp3")
        self.cache[key] = seg
        return seg

    def ar(self, text):           return self.say(text, VOICE_AR)
    def en(self, text):           return self.say(text, VOICE_EN_F)
    def en_slow(self, text):      return self.say(text, VOICE_EN_F, SLOW_RATE)
    def en_voice(self, text, v):  return self.say(text, VOICE_EN_M if v == "M" else VOICE_EN_F)


def pause(ms: int) -> AudioSegment:
    return AudioSegment.silent(duration=ms)


def repeat_pause(en: str) -> AudioSegment:
    """Long enough for a beginner to repeat the sentence out loud."""
    return pause(1800 + 550 * len(en.split()))


# ── Build Audio ───────────────────────────────────────────────────────────────
def build_audio(lesson: dict, following: dict | None, tmp_dir: Path) -> AudioSegment:
    s = Speaker(tmp_dir)
    out = AudioSegment.empty()

    def add(*segs):
        nonlocal out
        for seg in segs:
            out += seg

    # Consecutive Arabic lines are spoken in ONE call so the intonation flows naturally.

    # 1) Intro + goal + how to use the episode
    intro = random.choice(INTROS).format(topic=lesson["topic_ar"])
    add(s.ar(f"{intro} {lesson['goal_ar']} {HOW_TO_AR}"), pause(1200))

    # 2) Key words
    add(s.ar(AR_WORDS), pause(900))
    for w in lesson["vocab"]:
        add(s.en_slow(w["en"]), pause(700))
        add(s.ar(w["ar"]), pause(700))
        add(s.en(w["en"]), repeat_pause(w["en"]))
    add(pause(600))

    # 3) Sentences: situation -> normal -> slow -> repeat -> meaning + tip
    add(s.ar(AR_SENTS), pause(900))
    for i, sen in enumerate(lesson["sentences"]):
        label = f"{AR_SENTENCE} {ORDINALS_AR[i]}." if i < len(ORDINALS_AR) else f"{AR_SENTENCE} {i + 1}."
        add(s.ar(f"{label} {sen['situation_ar']}"), pause(700))
        add(s.en(sen["en"]), pause(900))
        add(s.en_slow(sen["en"]), repeat_pause(sen["en"]))
        add(s.ar(f"{AR_MEANING} {sen['meaning_ar']} {sen['tip_ar']}"), pause(700))
        add(s.en(sen["en"]), repeat_pause(sen["en"]))
        add(pause(500))

    # 4) Dialogue, played twice
    add(s.ar(f"{AR_DIALOGUE} {lesson['dialogue_intro_ar']}"), pause(1000))

    def play_dialogue():
        for line in lesson["dialogue"]:
            add(s.en_voice(line["en"], line["speaker"]), pause(650))

    play_dialogue()
    add(pause(800), s.ar(lesson["dialogue_outro_ar"]), pause(1000))
    play_dialogue()
    add(pause(1000))

    # 5) Quiz
    add(s.ar(AR_QUIZ), pause(1000))
    for q in lesson["quiz"]:
        add(s.ar(q["prompt_ar"]), pause(4500))
        add(s.ar(AR_ANSWER), pause(300))
        add(s.en(q["answer_en"]), repeat_pause(q["answer_en"]))

    # 6) Quick review + outro + teaser + closing (one Arabic call)
    add(s.ar(AR_REVIEW), pause(800))
    for sen in lesson["sentences"]:
        add(s.en(sen["en"]), pause(1500))
    ending = lesson["outro_ar"]
    if following:
        ending += " " + AR_NEXT.format(topic=following["topic_ar"])
    ending += " " + random.choice(CLOSINGS)
    add(pause(600), s.ar(ending), pause(1000))

    return out


def render(lesson: dict, following: dict | None, out_path: Path) -> int:
    """Build the episode to out_path. Returns duration in seconds."""
    if lesson.get("version") != 2:
        raise SystemExit(f"Lesson '{lesson['id']}' is still in the old format. Rewrite it first.")
    tmp_dir = out_path.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        audio = build_audio(lesson, following, tmp_dir)
        audio.export(str(out_path), format="mp3", bitrate="64k")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return len(audio) // 1000


# ── RSS Feed ──────────────────────────────────────────────────────────────────
def add_to_feed(title: str, url: str, pub_date: str, description: str,
                duration_secs: int, length_bytes: int):
    feed_path = Path("feed.xml")
    content = feed_path.read_text(encoding="utf-8")

    item = f"""
  <item>
    <title>{escape(title)}</title>
    <description>{escape(description)}</description>
    <pubDate>{pub_date}</pubDate>
    <enclosure url="{url}" length="{length_bytes}" type="audio/mpeg"/>
    <guid isPermaLink="true">{url}</guid>
    <itunes:duration>{duration_secs}</itunes:duration>
  </item>"""

    content = content.replace("</channel>", item + "\n</channel>")
    feed_path.write_text(content, encoding="utf-8")


# ── Git Push ──────────────────────────────────────────────────────────────────
def git_push(message: str):
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)


# ── Episode Number ────────────────────────────────────────────────────────────
def get_episode_number() -> int:
    return len(list(Path("episodes").glob("episode_*.mp3"))) + 1


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", metavar="LESSON_ID", help="build preview/<id>.mp3 only")
    ap.add_argument("--no-push", action="store_true", help="do not commit/push")
    args = ap.parse_args()

    if args.preview:
        lesson, following = pick_lesson(args.preview)
        out = Path("preview") / f"{lesson['id']}.mp3"
        out.parent.mkdir(exist_ok=True)
        print(f"Preview: {lesson['id']} ...")
        secs = render(lesson, following, out)
        print(f"Done: {out}  ({secs // 60}m {secs % 60}s)")
        return

    lesson, following = pick_lesson()
    today       = date.today().isoformat()
    episode_num = get_episode_number()
    episodes_dir = Path("episodes")
    episodes_dir.mkdir(exist_ok=True)
    # Episode number in the filename so two episodes on the same day never overwrite each other
    final_mp3 = episodes_dir / f"episode_{episode_num:03d}_{today}.mp3"

    print(f"Episode {episode_num} | {lesson['topic']} ({lesson['id']})")
    duration_secs = render(lesson, following, final_mp3)
    print(f"Duration: {duration_secs}s")

    episode_url = f"{BASE_URL}/episodes/{final_mp3.name}"
    add_to_feed(
        title=f"#{episode_num} - {lesson['topic']}",
        url=episode_url,
        pub_date=formatdate(localtime=True),
        description=lesson["description"],
        duration_secs=duration_secs,
        length_bytes=final_mp3.stat().st_size,
    )
    mark_used(lesson["id"])

    if args.no_push:
        print(f"Done (not pushed): {final_mp3}")
        return
    print("Pushing to GitHub...")
    git_push(f"episode {episode_num} - {lesson['id']}")
    print(f"Done! Episode #{episode_num}: {episode_url}")


if __name__ == "__main__":
    main()
