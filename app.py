from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__)

@app.route('/transcript', methods=['GET'])
def get_transcript():
    video_id = request.args.get('video_id')
    if not video_id:
        return jsonify({"success": False, "error": "Missing video_id"}), 400
    
    try:
        # Fetching transcript
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=['id', 'en'])
        text = " ".join([item['text'] for item in data])
        return jsonify({"success": True, "transcript": text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run()