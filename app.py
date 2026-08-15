import os
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

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-deyis-bunu")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///nova.db"
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "nova@example.com")

FREE_DAILY_LIMIT = 18
FREE_MAX_FILE_MB = 2
PLUS_MAX_FILE_MB = 25

FREE_MODEL = "gemma2-9b-it"
PLUS_MODEL = "llama-3.3-70b-versatile"

CODE_VALID_MINUTES = 10

SYSTEM_PROMPT = """
Senin adin Nova-dir. Sen komekci bir AI asistantsan.
Eger kimse senden "seni kim yaratdi" ve ya "yaradicin kimdir" deye sorussa,
cavab ver: "Meni Yusif Sahbazli yaratmisdir."
Hemise semimi, dostcasina ve Azerbaycan dilinde (istifadeci basqa dilde yazmasa) cavab ver.
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


def reset_daily_count_if_needed(user):
    today = str(date.today())
    if user.last_use_date != today:
        user.messages_used = 0
        user.last_use_date = today
        db.session.commit()


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
    reset_daily_count_if_needed(current_user)
    remaining = None
    if current_user.plan == "free":
        remaining = max(0, FREE_DAILY_LIMIT - current_user.messages_used)
    return render_template(
        "index.html",
        plan=current_user.plan,
        remaining=remaining,
        daily_limit=FREE_DAILY_LIMIT,
    )


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    reset_daily_count_if_needed(current_user)

    if current_user.plan == "free" and current_user.messages_used >= FREE_DAILY_LIMIT:
        return jsonify({
            "error": "limit",
            "message": f"Gundelik pulsuz mesaj limitine catmisan ({FREE_DAILY_LIMIT}/{FREE_DAILY_LIMIT}). Sabah yenilenecek, ya da Nova+ al."
        }), 403

    data = request.get_json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Bos mesaj"}), 400

    model = PLUS_MODEL if current_user.plan == "plus" else FREE_MODEL

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        reply = response.choices[0].message.content

        current_user.messages_used += 1
        db.session.commit()

        remaining = None
        if current_user.plan == "free":
            remaining = max(0, FREE_DAILY_LIMIT - current_user.messages_used)

        return jsonify({"reply": reply, "remaining": remaining})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Fayl tapilmadi"}), 400

    file = request.files["file"]
    file.seek(0, os.SEEK_END)
    size_mb = file.tell() / (1024 * 1024)
    file.seek(0)

    max_mb = PLUS_MAX_FILE_MB if current_user.plan == "plus" else FREE_MAX_FILE_MB
    if size_mb > max_mb:
        return jsonify({
            "error": "too_large",
            "message": f"Fayl cox boyukdur ({size_mb:.1f}MB). Senin planinda maksimum {max_mb}MB icazelidir."
        }), 413

    return jsonify({"status": "ok", "message": "Fayl qebul olundu."})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
else:
    with app.app_context():
        db.create_all()
