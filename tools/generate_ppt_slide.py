from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

prs = Presentation()
# Create a styled title slide inspired by the provided examples
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

# Title
title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.6)).text_frame
title = title_box.paragraphs[0]
title.text = "TECHNICAL APPROACH — AI Krishi"
title.font.size = Pt(28)
title.font.bold = True
title.font.color.rgb = RGBColor(20, 40, 85)

# Left: Documented 'Tool' circles row
circle_x = 0.5
colors = [(255,183,77),(102,187,106),(66,165,245),(239,83,80),(171,71,188)]
labels = ["NLP / NLU","OCR & STT","Web Frameworks","Workflow",
          "ML Platform"]
for i, (r,g,b) in enumerate(colors):
    left = Inches(circle_x + i*1.6)
    top = Inches(1.2)
    shp = slide.shapes.add_shape(1, left, top, Inches(1.2), Inches(1.2))
    fill = shp.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(r,g,b)
    line = shp.line
    line.fill.background()
    # Label
    lbl = slide.shapes.add_textbox(left-0.05, top+Inches(1.25), Inches(1.4), Inches(0.4)).text_frame
    p = lbl.paragraphs[0]
    p.text = labels[i]
    p.font.size = Pt(10)
    p.font.bold = True

# Center: Process Flow boxes with arrows
box_w = Inches(2.0)
box_h = Inches(0.6)
cx = Inches(1.0)
cy = Inches(2.4)
flow = ["Ingest", "Transcribe", "Intent & NLU", "Domain Services", "ML / Predictions", "Response"]
for i, txt in enumerate(flow):
    x = cx + i*(box_w + Inches(0.15))
    shp = slide.shapes.add_shape(1, x, cy, box_w, box_h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor(250,250,250)
    shp.line.color.rgb = RGBColor(200,200,200)
    tf = shp.text_frame
    p = tf.paragraphs[0]
    p.text = txt
    p.font.size = Pt(12)
    p.font.bold = True
    # arrows between
    if i < len(flow)-1:
        start_x = x+box_w
        start_y = cy+box_h/2
        end_x = x+box_w+Inches(0.15)
        end_y = start_y
        line = slide.shapes.add_connector(1, start_x, start_y, end_x+box_w-Inches(0.15), end_y)
        line.line.width = Pt(2)
        line.line.color.rgb = RGBColor(120,120,120)

# Right: Component boxes stacked
comp_x = Inches(0.5)
comp_y = Inches(3.8)
comp_w = Inches(4.6)
comp_h = Inches(0.5)
components = ["Interface Layer: multi-channel ingress","API & Orchestration: gateway, auth",
              "Domain Services: marketplace, auction","ML Inference: model serving","Data Layer: RDBMS, storage"]
for i, c in enumerate(components):
    y = comp_y + i*(comp_h + Inches(0.12))
    box = slide.shapes.add_shape(1, Inches(5.5), y, comp_w, comp_h)
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(245,245,250)
    box.line.color.rgb = RGBColor(200,200,210)
    tf = box.text_frame
    p = tf.paragraphs[0]
    p.text = c
    p.font.size = Pt(11)

# Footer note
footer = slide.shapes.add_textbox(Inches(0.5), Inches(6.6), Inches(9), Inches(0.4)).text_frame
f = footer.paragraphs[0]
f.text = "Example: Farmer calls → system transcribes → predicts & replies by voice, updates dashboard."
f.font.size = Pt(11)
f.font.color.rgb = RGBColor(90, 90, 90)

# Save
import os
os.makedirs('output', exist_ok=True)
prs.save('output/AI_Krishi_Styled_Slide.pptx')
print('Saved output/AI_Krishi_Styled_Slide.pptx')
