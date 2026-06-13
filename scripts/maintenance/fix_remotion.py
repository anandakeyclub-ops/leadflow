f = open('reel_generator.py', 'r', encoding='utf-8')
c = f.read()
f.close()

c = c.replace(
    'cwd=str(REMOTION_PROJECT),\n            cwd=str(REMOTION_PROJECT),',
    'cwd=str(REMOTION_PROJECT),'
)

f = open('reel_generator.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print('done')