import os
import io
import random
import requests
from datetime import date, datetime, timedelta

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from groq import Groq

try:
    from PyPDF2 import PdfReader
    PDF_SUPPORT = True
except Exception:
    PDF_SUPPORT = False

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-deyis-bunu")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///nova.db"
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "nova@example.com")

FREE_MODEL = "openai/gpt-oss-20b"
PLUS_MODEL = "openai/gpt-oss-120b"

FREE_PDF_MAX_PAGES = 20  # Free planda PDF max 20 sehife
# Sekiller her iki planda limitsiz
# Nova+ da her ne&#39; boyuklukde/formatda fayl serbestdir

CODE_VALID_MINUTES = 10

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

SYSTEM_PROMPT = """
Senin adin Nova-dir. Sen komekci bir AI asistantsan.

Yalniz kimse senden AYDIN SEKILDE "seni kim yaratdi", "yaradicin kimdir" ve ya
"seni kim qurdu" kimi birbasa bir sual sorussa (yaxud "seni men yaratmisam" kimi
bir iddia etse), cavab ver: "Meni Yusif Sahbazli yaratmisdir." Basqa hec bir halda
bu melumati oz-ozune paylasma.

Istifadeci sual yazmasa da, adi bir cumle, fikir ve ya ifade yazsa da, ona tebii
sekilde reaksiya ver - sanki real bir sohbetdesen. Her mesaja "salam" ile
baslamaga ehtiyac yoxdur - yalniz istifadeci ozu salamlasanda salamlas, aksinede
birbasa movzuya keç.

Hemise semimi, dostcasina ve Azerbaycan dilinde (istifadeci basqa dilde yazmasa)
cavab ver.
"""


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    plan = db.Column(db.String(20), default="free")
    messages_used = db.Column(db.Integer, default=0)
    last_use_date = db.Column(db.String(10), default="")


class VerificationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def send_code_email(to_email, code):
    if not BREVO_API_KEY:
        print(f"[DEV] {to_email} ucun kod: {code}")
        return

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }
    payload = {
        "sender": {"name": "Nova", "email": SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": f"Nova giris kodun: {code}",
        "htmlContent": f"<p>Nova giris kodun: <b>{code}</b></p><p>Bu kod {CODE_VALID_MINUTES} deqiqe etibarlidir.</p>",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code >= 300:
        raise Exception(f"Brevo error: {response.status_code} {response.text}")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email or "@" not in email:
            flash("Duzgun email daxil et.")
            return redirect(url_for("login"))

        code = f"{random.randint(0, 999999):06d}"
        db.session.add(VerificationCode(email=email, code=code))
        db.session.commit()

        try:
            send_code_email(email, code)
        except Exception:
            flash("Kod gonderile bilmedi. Bir az sonra yeniden cehd et.")
            return redirect(url_for("login"))

        session["pending_email"] = email
        return redirect(url_for("verify"))

    return render_template("login.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        entered = request.form.get("code", "").strip()

        record = (
            VerificationCode.query
            .filter_by(email=email, code=entered)
            .order_by(VerificationCode.id.desc())
            .first()
        )

        if not record:
            flash("Kod sehvdir.")
            return redirect(url_for("verify"))

        if datetime.utcnow() - record.created_at > timedelta(minutes=CODE_VALID_MINUTES):
            flash("Kodun vaxti bitib. Yenisini iste.")
            return redirect(url_for("login"))

        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, plan="free", messages_used=0, last_use_date=str(date.today()))
            db.session.add(user)
            db.session.commit()

        VerificationCode.query.filter_by(email=email).delete()
        db.session.commit()

        session.pop("pending_email", None)
        login_user(user)
        return redirect(url_for("home"))

    return render_template("verify.html", email=email)


@app.route("/resend")
def resend():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("login"))

    code = f"{random.randint(0, 999999):06d}"
    db.session.add(VerificationCode(email=email, code=code))
    db.session.commit()

    try:
        send_code_email(email, code)
        flash("Yeni kod gonderildi.")
    except Exception:
        flash("Kod gonderile bilmedi.")

    return redirect(url_for("verify"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    return render_template("index.html", plan=current_user.plan)


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    history = data.get("history", [])
    if not user_message:
        return jsonify({"error": "Bos mesaj"}), 400

    if not isinstance(history, list):
        history = []
    # Sadece duzgun formatdaki mesajlari saxla, cox uzun tarixceni kes (son 20 mesaj)
    clean_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-20:]

    model = PLUS_MODEL if current_user.plan == "plus" else FREE_MODEL

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *clean_history,
                {"role": "user", "content": user_message},
            ],
        )
        reply = response.choices[0].message.content
        current_user.messages_used += 1
        db.session.commit()
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    try:
        if "file" not in request.files:
            return jsonify({"error": "Fayl tapilmadi"}), 400

        file = request.files["file"]
        filename = file.filename or "fayl"
        ext = os.path.splitext(filename)[1].lower()

        file.seek(0, os.SEEK_END)
        size_mb = file.tell() / (1024 * 1024)
        file.seek(0)

        # Nova+ - hec bir mehdudiyyet, her cure fayl/sekil
        if current_user.plan == "plus":
            return jsonify({"status": "ok", "message": f"{filename} qebul olundu."})

        # Free plan - sekiller (her cure format) limitsiz
        if ext in IMAGE_EXTENSIONS or (file.mimetype or "").startswith("image/"):
            return jsonify({"status": "ok", "message": f"{filename} qebul olundu."})

        # Free plan - PDF: mumkunse sehife sayini yoxla, kitabxana yoxdursa sadece olcu ile davam et
        if ext == ".pdf" and PDF_SUPPORT:
            try:
                reader = PdfReader(io.BytesIO(file.read()))
                page_count = len(reader.pages)
                file.seek(0)
            except Exception:
                page_count = None

            if page_count is not None:
                if page_count > FREE_PDF_MAX_PAGES:
                    return jsonify({
                        "error": "too_large",
                        "message": f"Bu PDF {page_count} sehifedir. Pulsuz planda maksimum {FREE_PDF_MAX_PAGES} sehife icazelidir. Nova+ ile limitsiz."
                    }), 413
                return jsonify({"status": "ok", "message": f"{filename} qebul olundu ({page_count} sehife)."})

        # Free plan - basqa fayl tipleri (ve PDF kitabxanasi yoxdursa): 5MB limiti
        if size_mb > 5:
            return jsonify({
                "error": "too_large",
                "message": f"Fayl cox boyukdur ({size_mb:.1f}MB). Pulsuz planda maksimum 5MB. Nova+ ile limitsiz."
            }), 413

        return jsonify({"status": "ok", "message": f"{filename} qebul olundu."})

    except Exception as e:
        return jsonify({"error": "server_error", "message": f"Xeta: {str(e)}"}), 500


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
else:
    with app.app_context():
        db.create_all()
