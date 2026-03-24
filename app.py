from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
import logging
import traceback
import time
import os
import json
import hashlib
import xml.etree.ElementTree as ET
import subprocess
import tempfile
import glob
import re

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ====== CONFIG (edit if needed) ======
COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", "/home/allbibek/ytta/cookies.txt")
VENV_PYTHON = os.environ.get("VENV_PYTHON", "/home/allbibek/ytta/venv/bin/python")
CACHE_DIR = os.environ.get("YT_CACHE_DIR", "/home/allbibek/cache/yt_transcripts")
CACHE_TTL_SECONDS = int(os.environ.get("YT_CACHE_TTL_SECONDS", "86400"))  # 1 day
LANGUAGES = ["id", "en"]
PROXY_URL = os.environ.get("YT_PROXY_URL", "")
# =====================================

def _video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def _cache_file(video_id: str, languages: list[str]) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(f"{video_id}|{','.join(languages)}".encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.json")

def cache_get(video_id: str, languages: list[str]):
    path = _cache_file(video_id, languages)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        ts = float(payload.get("ts", 0))
        if time.time() - ts > CACHE_TTL_SECONDS:
            return None
        return payload.get("text")
    except Exception:
        return None

def cache_set(video_id: str, languages: list[str], text: str):
    path = _cache_file(video_id, languages)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "text": text}, f, ensure_ascii=False)
    os.replace(tmp, path)

def _vtt_to_text(vtt: str) -> str:
    lines = []
    for line in vtt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.upper() == "WEBVTT":
            continue
        if "-->" in s:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        lines.append(s)
    return " ".join(lines).strip()

def _run_ytdlp(args: list[str], timeout: int = 120):
    """
    Always run yt-dlp using the venv python explicitly:
      /home/allbibek/ytta/venv/bin/python -m yt_dlp ...
    This avoids uwsgi/PATH confusion.
    """
    cmd = [VENV_PYTHON, "-m", "yt_dlp"] + args
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)

def fetch_with_ytdlp(video_id: str, languages: list[str]) -> str:
    url = _video_url(video_id)

    with tempfile.TemporaryDirectory() as tmp:
        outtmpl = os.path.join(tmp, "%(id)s.%(ext)s")
        lang_arg = ",".join(languages)

        base_args = [
            "--cookies", COOKIES_FILE,
            "--remote-components", "ejs:github",
            "--skip-download", "--sub-format", "vtt", 
            "--sub-langs", lang_arg, "-o", outtmpl, url
        ]

        if PROXY_URL:
            base_args = ["--proxy", PROXY_URL] + base_args

        attempts = [
            base_args + ["--write-subs"],
            base_args + ["--write-auto-subs"],
        ]

        last_err = None
        for a in attempts:
            p = _run_ytdlp(a, timeout=120)
            if p.returncode != 0:
                last_err = RuntimeError((p.stderr or p.stdout or "yt-dlp failed").strip())
                continue

            vtts = sorted(glob.glob(os.path.join(tmp, f"{video_id}*.vtt")))
            if not vtts:
                vtts = sorted(glob.glob(os.path.join(tmp, "*.vtt")))
            if not vtts:
                last_err = RuntimeError("yt-dlp succeeded but no .vtt file produced")
                continue

            # prefer by language order if possible
            preferred = None
            for lang in languages:
                for f in vtts:
                    if f".{lang}." in f or f".{lang}-" in f or f".{lang}_" in f:
                        preferred = f
                        break
                if preferred:
                    break

            chosen = preferred or vtts[0]
            with open(chosen, "r", encoding="utf-8", errors="ignore") as f:
                vtt = f.read()

            text = _vtt_to_text(vtt)
            if not text:
                last_err = RuntimeError("Empty transcript after parsing VTT")
                continue

            return text

        raise last_err or RuntimeError("Failed to fetch subtitles via yt-dlp")

@app.route("/transcript", methods=["GET"])
def transcript():
    video_id = request.args.get("video_id", "").strip()
    if not video_id:
        return jsonify({"success": False, "error": "Missing video_id parameter"}), 400

    cached = cache_get(video_id, LANGUAGES)
    if cached:
        return jsonify({"success": True, "video_id": video_id, "transcript": cached, "cached": True, "source": "cache"}), 200

    # A) try youtube-transcript-api with small retry for ParseError
    try:
        last_err = None
        for delay in (0, 2, 6):
            try:
                if delay:
                    time.sleep(delay)
                data = YouTubeTranscriptApi.get_transcript(video_id, languages=LANGUAGES)
                full_text = " ".join([item.get("text", "") for item in data]).strip()
                if full_text:
                    cache_set(video_id, LANGUAGES, full_text)
                    return jsonify({"success": True, "video_id": video_id, "transcript": full_text, "cached": False, "source": "youtube-transcript-api"}), 200
                last_err = RuntimeError("Empty transcript returned")
            except ET.ParseError as e:
                last_err = e
                continue
        raise last_err if last_err else ET.ParseError("no element found: line 1, column 0")

    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
        app.logger.warning(f"youtube-transcript-api known error video_id={video_id} type={type(e).__name__} msg={e}")
        # fall through to yt-dlp
    except Exception as e:
        app.logger.warning(f"youtube-transcript-api failed; fallback video_id={video_id} type={type(e).__name__} msg={e}")
        app.logger.warning(traceback.format_exc())

    # B) fallback yt-dlp
    try:
        text = fetch_with_ytdlp(video_id, LANGUAGES)
        cache_set(video_id, LANGUAGES, text)
        return jsonify({"success": True, "video_id": video_id, "transcript": text, "cached": False, "source": "yt-dlp"}), 200
    except Exception as e:
        app.logger.error(f"yt-dlp failed video_id={video_id} type={type(e).__name__} msg={e}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "error_type": "UpstreamFailure",
            "message": str(e),
            "hint": "youtube-transcript-api gave non-XML/empty response and yt-dlp also failed."
        }), 502

if __name__ == "__main__":
    app.run()