import re

path = r"C:\Users\Dana\Desktop\leadflow\app\workers\send_email_sequence.py"
with open(path, "r", encoding="utf-8") as f:
    code = f.read()

# Replace OAuth with SMTP
old = '''def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    creds  = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(
                str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)'''

new = '''def get_gmail_service():
    import smtplib, ssl, os
    app_pw = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    sender = os.getenv("GMAIL_SENDER", "romy@taxcasereview.org")
    if not app_pw:
        raise ValueError("GMAIL_APP_PASSWORD not set in .env")
    ctx = ssl.create_default_context()
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx)
    server.login(sender, app_pw)
    return server'''

if old in code:
    code = code.replace(old, new)
    print("OAuth -> SMTP: replaced")
else:
    print("Pattern not found - check manually")

# Replace send via Gmail API with SMTP sendmail
old2 = '''    raw    = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}).execute()
    return result'''

new2 = '''    service.sendmail(sender_email, to_email, msg.as_string())
    return {"status": "sent"}'''

if old2 in code:
    code = code.replace(old2, new2)
    print("API send -> SMTP send: replaced")
else:
    print("Send pattern not found")

with open(path, "w", encoding="utf-8") as f:
    f.write(code)
print("Done - file updated")