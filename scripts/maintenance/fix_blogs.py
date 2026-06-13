f = open('generate_topic_blogs.py', 'r', encoding='utf-8')
c = f.read()
f.close()

old = '- End every post with a low-pressure CTA linking to {site} and phone {phone}'
new = '''- End every post with a low-pressure CTA linking to {site} and phone {phone}
- Add one mid-article inline CTA after the most urgent section: 
  "**Need help now? [See your options in 60 seconds]({site}/quiz) or call {phone}**"
- Add one "Pro Tip" callout box mid-article using: > 💡 **Pro Tip:** ...'''.format(site='{site}', phone='{phone}')

if old in c:
    c = c.replace(old, new)
    print('Added mid-article CTAs to writing rules')
else:
    print('Pattern not found')

f = open('generate_topic_blogs.py', 'w', encoding='utf-8')
f.write(c)
f.close()