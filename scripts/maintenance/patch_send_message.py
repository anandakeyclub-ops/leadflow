"""
patch_send_message.py
Fixes send_message() to use SMTP instead of Gmail API.
Run: python patch_send_message.py
"""
import re

path = r"C:\Users\Dana\Desktop\leadflow\app\workers\send_email_sequence.py"

with open(path, "r", encoding="utf-8") as f:
    code = f.read()

# Find and replace the Gmail API send call
# Handles both versions of the line
patterns = [
    # Pattern 1: base64 + service.users()
    (
        r'raw\s*=\s*base64\.urlsafe_b64encode\(msg\.as_bytes\(\)\)\.decode\(\)\s*\n\s*result\s*=\s*service\.users\(\)\.messages\(\)\.send\([^)]+\)\.execute\(\)\s*\n\s*return result',
        '    service.sendmail("romy@taxcasereview.org", to_email, msg.as_string())\n    return {"status": "sent"}'
    ),
    # Pattern 2: just service.users() send
    (
        r'result\s*=\s*service\.users\(\)\.messages\(\)\.send\([^)]+\)\.execute\(\)\s*\n\s*return result',
        '    service.sendmail("romy@taxcasereview.org", to_email, msg.as_string())\n    return {"status": "sent"}'
    ),
    # Pattern 3: any remaining service.users() calls
    (
        r'service\.users\(\)\.messages\(\)\.send\([^)]+\)\.execute\(\)',
        'service.sendmail("romy@taxcasereview.org", to_email, msg.as_string())'
    ),
]

found = False
for pattern, replacement in patterns:
    new_code, count = re.subn(pattern, replacement, code, flags=re.DOTALL)
    if count > 0:
        code = new_code
        print(f"Fixed {count} occurrence(s) with pattern: {pattern[:50]}...")
        found = True

if not found:
    # Manual check
    idx = code.find("service.users()")
    if idx >= 0:
        print(f"Found service.users() at position {idx}")
        print(f"Context: {code[idx-100:idx+100]}")
    else:
        print("service.users() not found - may already be fixed")

# Also remove base64 import if present since we no longer need it
# (keep it if used elsewhere)
if "urlsafe_b64encode" not in code and "import base64" in code:
    code = code.replace("import base64\n", "")
    print("Removed unused base64 import")

with open(path, "w", encoding="utf-8") as f:
    f.write(code)

print("Saved.")

# Verify
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("Syntax OK")
except py_compile.PyCompileError as e:
    print(f"Syntax error: {e}")
