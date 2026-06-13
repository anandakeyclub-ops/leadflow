path = r"C:\Users\Dana\Desktop\leadflow\app\workers\send_email_sequence.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Fix line 334 (index 333) - remove extra indentation
lines[333] = '    service.sendmail("romy@taxcasereview.org", to_email, msg.as_string())\n'

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Fixed line 334")

# Verify syntax
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("Syntax OK")
except py_compile.PyCompileError as e:
    print(f"Syntax error: {e}")
