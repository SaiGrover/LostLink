import json
from pathlib import Path
from PIL import Image, ImageDraw

root=Path('evaluation/pilot')
objects=json.loads((root/'dataset.json').read_text())['sources']
sheet=Image.new('RGB',(1200, ((len(objects)+7)//8)*150),'#f5f5ef')
draw=ImageDraw.Draw(sheet)
for index,obj in enumerate(objects):
    with Image.open(root/obj['image']) as original:
        im=original.convert('RGB')
        im.thumbnail((135,108))
    x=(index%8)*150+7; y=(index//8)*150+5
    sheet.paste(im,(x,y))
    draw.text((x,y+112),f"{index+1} {obj['category'][:9]}",fill='#17352c')
sheet.save(root/'contact-sheet.jpg',quality=88)
