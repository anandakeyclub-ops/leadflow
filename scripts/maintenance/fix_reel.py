f = open('social_media_poster.py', 'r', encoding='utf-8')
c = f.read()
f.close()

old = '''    payload: dict = {"message": text, "link": SITE_URL}
    if image_url:
        payload["image_url"] = image_url'''

new = '''    payload: dict = {"message": text, "link": SITE_URL, "reel": False}
    if image_url:
        payload["image_url"] = image_url
        if image_url.endswith(".mp4") or "video" in image_url:
            payload["reel"] = True'''

if old in c:
    c = c.replace(old, new)
    print("Fixed payload")
else:
    print("Pattern not found")

f = open('social_media_poster.py', 'w', encoding='utf-8')
f.write(c)
f.close()