from flask import Flask, request, jsonify, render_template
from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """
Sənin adın Nova-dır. Sən köməkçi bir AI asistantsan.
Əgər kimsə səndən "səni kim yaratdı" və ya "yaradıcın kimdir" deyə soruşsa,
cavab ver: "Məni Yusif Şahbazlı yaratmışdır."
Həmişə səmimi, dostcasına və Azərbaycan dilində (istifadəçi başqa dildə yazmasa) cavab ver.
"""

conversation_history = [
    {"role": "system", "content": SYSTEM_PROMPT}
]


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Boş mesaj"}), 400

    conversation_history.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=conversation_history,
        )
        reply = response.choices[0].message.content
        conversation_history.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)