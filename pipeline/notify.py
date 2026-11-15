"""운영자에게 메일 알림. Gmail 앱 비밀번호로 보낸다.

환경변수: GMAIL_USER(보내는 계정), GMAIL_APP_PW(앱 비밀번호), ALERT_TO(받는 주소, 없으면 GMAIL_USER)
설정이 없으면 보내지 않고 출력만 한다. 메일 실패가 본 작업을 멈추게 하지 않는다.

시험: python pipeline/notify.py   → '[nowcast] 알림 시험' 메일
"""
import os
import smtplib
import sys
from email.message import EmailMessage


def send(subject, body):
    user = os.environ.get("GMAIL_USER")
    pw = (os.environ.get("GMAIL_APP_PW") or "").replace(" ", "")
    to = os.environ.get("ALERT_TO") or user
    if not (user and pw):
        print(f"[notify] 메일 설정 없음 → 보내지 않음: {subject}", flush=True)
        return False
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = f"[nowcast] {subject}", user, to
    m.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.send_message(m)
        print(f"[notify] 보냄: {subject}", flush=True)
        return True
    except Exception as e:
        print(f"[notify] 메일 실패({type(e).__name__}): {subject}", flush=True)
        return False


if __name__ == "__main__":
    sys.exit(0 if send("알림 시험", "나우캐스트 MCP 서버에서 보낸 시험 메일입니다.") else 1)
