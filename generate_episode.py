import anthropic
import os
import re
import random
import subprocess
from datetime import date
from email.utils import formatdate
from pathlib import Path
from dotenv import load_dotenv

# Always run from the script's directory
os.chdir(Path(__file__).parent)
load_dotenv()

# Add ffmpeg to PATH so pydub can find it
FFMPEG_BIN = r"C:\Users\king2\Downloads\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build\bin"
os.environ["PATH"] = FFMPEG_BIN + ";" + os.environ.get("PATH", "")

from gtts import gTTS
from pydub import AudioSegment

# ── Config ────────────────────────────────────────────────────────────────────
FFMPEG  = r"C:\Users\king2\Downloads\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build\bin\ffmpeg.exe"
FFPROBE = r"C:\Users\king2\Downloads\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build\bin\ffprobe.exe"

BASE_URL = "https://m509r2.github.io/english-podcast"

AudioSegment.converter = FFMPEG
AudioSegment.ffmpeg    = FFMPEG
AudioSegment.ffprobe   = FFPROBE

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


# ── Claude API ────────────────────────────────────────────────────────────────
def generate_lesson(topic: str, episode_num: int) -> tuple[str, str, str]:
    """Returns (english_text, arabic_text, arabic_description)."""
    client = anthropic.Anthropic()

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system="You are a friendly English teacher creating short podcast lessons for Arabic speakers. Keep sentences simple, clear and practical.",
        messages=[{
            "role": "user",
            "content": f"""Create a 5-minute English podcast lesson. Episode number: {episode_num}. Topic: {topic}

Use EXACTLY this format (keep all markers):

---ENGLISH---
Welcome to Learn English Daily! I'm your English teacher, and this is episode {episode_num}.
Today's topic is: {topic}.
Let's begin!

[Write exactly 7 simple, practical sentences about {topic}. After each sentence say "Repeat:" then repeat it.]

That's all for today. You did a great job! Keep practicing, and I will see you tomorrow for a new lesson. Goodbye!
---ARABIC---
الآن شرح الجمل بالعربية:

[For each of the 7 sentences write:
number) the English sentence
معناها: the Arabic translation
(blank line between each)]

أحسنت! استمع للدرس أكثر من مرة وكرر الجمل بصوت عالٍ. أراك غداً!
---DESCRIPTION---
[Write 2 sentences in Arabic summarizing what the listener will learn in this episode. Start with "في هذه الحلقة"]"""
        }],
    )

    text = response.content[0].text
    en_match   = re.search(r"---ENGLISH---\s*(.*?)\s*---ARABIC---",      text, re.DOTALL)
    ar_match   = re.search(r"---ARABIC---\s*(.*?)\s*---DESCRIPTION---",   text, re.DOTALL)
    desc_match = re.search(r"---DESCRIPTION---\s*(.*)",                    text, re.DOTALL)

    en_text   = en_match.group(1).strip()   if en_match   else text
    ar_text   = ar_match.group(1).strip()   if ar_match   else ""
    desc_text = desc_match.group(1).strip() if desc_match else f"درس إنجليزي يومي عن {topic}."
    return en_text, ar_text, desc_text


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


# ── Main ──────────────────────────────────────────────────────────────────────
def get_episode_number() -> int:
    """Count existing final episodes to determine next episode number."""
    episodes_dir = Path("episodes")
    existing = list(episodes_dir.glob("episode_*.mp3"))
    # exclude tmp files
    final = [f for f in existing if not f.stem.startswith("tmp_")]
    return len(final) + 1


def main():
    topic       = random.choice(TOPICS)
    today       = date.today().isoformat()
    episode_num = get_episode_number()

    episodes_dir = Path("episodes")
    episodes_dir.mkdir(exist_ok=True)

    final_mp3 = episodes_dir / f"episode_{today}.mp3"
    tmp_en    = episodes_dir / f"tmp_en_{today}.mp3"
    tmp_ar    = episodes_dir / f"tmp_ar_{today}.mp3"

    print(f"Episode {episode_num} | Topic: {topic}")
    en_text, ar_text, description = generate_lesson(topic, episode_num)

    print("Generating audio...")
    gTTS(en_text, lang="en").save(str(tmp_en))
    gTTS(ar_text, lang="ar").save(str(tmp_ar))

    en_audio    = AudioSegment.from_mp3(tmp_en)
    ar_audio    = AudioSegment.from_mp3(tmp_ar)
    final_audio = en_audio + AudioSegment.silent(duration=700) + ar_audio
    final_audio.export(str(final_mp3), format="mp3")

    tmp_en.unlink(missing_ok=True)
    tmp_ar.unlink(missing_ok=True)

    duration_secs = len(final_audio) // 1000

    episode_url = f"{BASE_URL}/episodes/episode_{today}.mp3"
    add_to_feed(
        title=f"#{episode_num} – {topic}",
        url=episode_url,
        pub_date=formatdate(localtime=True),
        description=description,
        duration_secs=duration_secs,
    )

    print("Pushing to GitHub...")
    git_push(today)
    print(f"Done! Episode #{episode_num} → {episode_url}")


if __name__ == "__main__":
    main()
