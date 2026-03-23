# app.py
from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "YouTube Transcript API is Running!"

@app.route('/transcript', methods=['GET'])
def get_transcript():
    video_id = request.args.get('video_id')
    
    if not video_id:
        return jsonify({"success": False, "error": "Missing video_id parameter"}), 400

    try:
        # Fetching transcript with primary languages: Indonesian and English
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['id', 'en'])
        
        # Combine all text segments into one string
        full_text = " ".join([t['text'] for t in transcript_list])
        
        return jsonify({
            "success": True,
            "video_id": video_id,
            "transcript": full_text
        })
    except Exception as e:
        return jsonify({
            "success": False, 
            "error": str(e)
        }), 500

if __name__ == "__main__":
    # Render uses environment variable PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)