import anthropic
import asyncio
import os
import re
import random
import subprocess
from datetime import date
from email.utils import formatdate
from pathlib import Path
from dotenv import load_dotenv

import edge_tts
from pydub import AudioSegment

# Always run from the script's directory
os.chdir(Path(__file__).parent)
load_dotenv()

# Add ffmpeg to PATH
FFMPEG_BIN = r"C:\Users\king2\Downloads\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build\bin"
os.environ["PATH"] = FFMPEG_BIN + ";" + os.environ.get("PATH", "")

# Voices
VOICE_AR = "ar-SA-ZariyahNeural"   # Arabic - Saudi female
VOICE_EN = "en-US-AriaNeural"      # English - US female

BASE_URL = "https://m509r2.github.io/english-podcast"

TOPICS = [
    "Greetings and introductions",
    "Daily routines",
    "At the restaurant",
    "Shopping basics",
    "Asking for directions",
    "Talking about family",
    "Health and doctor appointments",
    "Travel and airport",
    "Time and schedules",
    "At the office",
    "Making phone calls",
    "Weather and seasons",
    "Hobbies and free time",
    "Food and cooking",
    "Education and learning",
    "Numbers and prices",
    "Colors and descriptions",
    "Days, months and dates",
    "At the hotel",
    "Emergencies",
]

PAUSE_SHORT = AudioSegment.silent(duration=600)
PAUSE_LONG  = AudioSegment.silent(duration=1200)


# ── Claude API ────────────────────────────────────────────────────────────────
def generate_lesson(topic: str, episode_num: int) -> dict:
    client = anthropic.Anthropic()

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=3000,
        system="You are creating an English podcast lesson for Arabic-speaking beginners. Keep English sentences very simple and practical.",
        messages=[{
            "role": "user",
            "content": f"""Create episode {episode_num} about: {topic}

Use EXACTLY this format with all markers:

---INTRO_AR---
[2-3 sentences Arabic welcome. Greet the listener warmly, say today's topic is {topic}, encourage them to listen and repeat]
---SENTENCES---
[Write exactly 7 sentences. For each one use this format:]
EN: [simple English sentence about {topic}]
AR: [Arabic translation and brief explanation]

---OUTRO_AR---
[2 sentences Arabic closing. Praise the listener, encourage daily practice, say see you tomorrow]
---DESCRIPTION---
[2 sentences Arabic description for Spotify starting with: في هذه الحلقة]"""
        }],
    )

    text = response.content[0].text

    intro_ar  = re.search(r"---INTRO_AR---\s*(.*?)\s*---SENTENCES---",   text, re.DOTALL)
    sentences = re.search(r"---SENTENCES---\s*(.*?)\s*---OUTRO_AR---",    text, re.DOTALL)
    outro_ar  = re.search(r"---OUTRO_AR---\s*(.*?)\s*---DESCRIPTION---",  text, re.DOTALL)
    desc      = re.search(r"---DESCRIPTION---\s*(.*)",                     text, re.DOTALL)

    # Parse EN/AR sentence pairs
    pairs = []
    if sentences:
        en_items = re.findall(r"EN:\s*(.+)", sentences.group(1))
        ar_items = re.findall(r"AR:\s*(.+)", sentences.group(1))
        pairs = list(zip(en_items, ar_items))

    return {
        "intro_ar":    intro_ar.group(1).strip()  if intro_ar  else f"أهلاً! موضوع اليوم: {topic}",
        "pairs":       pairs,
        "outro_ar":    outro_ar.group(1).strip()  if outro_ar  else "أحسنت! أراك غداً.",
        "description": desc.group(1).strip()      if desc      else f"درس إنجليزي عن {topic}.",
    }


# ── TTS ───────────────────────────────────────────────────────────────────────
async def tts(text: str, voice: str, path: str):
    await edge_tts.Communicate(text, voice).save(path)


def speak_ar(text: str, path: str):
    asyncio.run(tts(text, VOICE_AR, path))


def speak_en(text: str, path: str):
    asyncio.run(tts(text, VOICE_EN, path))


def load(path: str) -> AudioSegment:
    return AudioSegment.from_file(path, format="mp3")


# ── Build Audio ───────────────────────────────────────────────────────────────
def build_audio(lesson: dict, tmp_dir: Path) -> AudioSegment:
    parts = []
    i = 0

    # Intro (Arabic)
    f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
    speak_ar(lesson["intro_ar"], f)
    parts.append(load(f))
    parts.append(PAUSE_LONG)

    # Sentences
    for idx, (en, ar) in enumerate(lesson["pairs"], 1):
        # Sentence number in Arabic
        num_ar = f"الجملة {idx}:"
        f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
        speak_ar(num_ar, f)
        parts.append(load(f))
        parts.append(PAUSE_SHORT)

        # English sentence
        f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
        speak_en(en, f)
        parts.append(load(f))
        parts.append(PAUSE_SHORT)

        # Repeat cue + English again
        f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
        speak_en("Repeat.", f)
        parts.append(load(f))
        parts.append(PAUSE_SHORT)

        f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
        speak_en(en, f)
        parts.append(load(f))
        parts.append(PAUSE_SHORT)

        # Arabic explanation
        f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
        speak_ar(ar, f)
        parts.append(load(f))
        parts.append(PAUSE_LONG)

    # Outro (Arabic)
    f = str(tmp_dir / f"seg_{i:02d}.mp3"); i += 1
    speak_ar(lesson["outro_ar"], f)
    parts.append(load(f))

    final = parts[0]
    for p in parts[1:]:
        final = final + p
    return final


# ── RSS Feed ──────────────────────────────────────────────────────────────────
def add_to_feed(title: str, url: str, pub_date: str, description: str, duration_secs: int):
    feed_path = Path("feed.xml")
    content = feed_path.read_text(encoding="utf-8")

    item = f"""
  <item>
    <title>{title}</title>
    <description>{description}</description>
    <pubDate>{pub_date}</pubDate>
    <enclosure url="{url}" length="0" type="audio/mpeg"/>
    <guid isPermaLink="true">{url}</guid>
    <itunes:duration>{duration_secs}</itunes:duration>
  </item>"""

    content = content.replace("</channel>", item + "\n</channel>")
    feed_path.write_text(content, encoding="utf-8")


# ── Git Push ──────────────────────────────────────────────────────────────────
def git_push(today: str):
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", f"episode {today}"], check=True)
    subprocess.run(["git", "push"], check=True)


# ── Episode Number ────────────────────────────────────────────────────────────
def get_episode_number() -> int:
    final = [f for f in Path("episodes").glob("episode_*.mp3")
             if not f.stem.startswith("tmp_")]
    return len(final) + 1


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    topic       = random.choice(TOPICS)
    today       = date.today().isoformat()
    episode_num = get_episode_number()

    episodes_dir = Path("episodes")
    episodes_dir.mkdir(exist_ok=True)
    tmp_dir = episodes_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    final_mp3 = episodes_dir / f"episode_{today}.mp3"

    print(f"Episode {episode_num} | {topic}")

    print("Generating lesson with Claude...")
    lesson = generate_lesson(topic, episode_num)
    print(f"Got {len(lesson['pairs'])} sentences")

    print("Building audio segments...")
    final_audio = build_audio(lesson, tmp_dir)
    final_audio.export(str(final_mp3), format="mp3", bitrate="64k")

    # Cleanup temp segments
    for f in tmp_dir.iterdir():
        f.unlink()
    tmp_dir.rmdir()

    duration_secs = len(final_audio) // 1000
    print(f"Duration: {duration_secs}s")

    episode_url = f"{BASE_URL}/episodes/episode_{today}.mp3"
    add_to_feed(
        title=f"#{episode_num} - {topic}",
        url=episode_url,
        pub_date=formatdate(localtime=True),
        description=lesson["description"],
        duration_secs=duration_secs,
    )

    print("Pushing to GitHub...")
    git_push(today)
    print(f"Done! Episode #{episode_num}: {episode_url}")


if __name__ == "__main__":
    main()
