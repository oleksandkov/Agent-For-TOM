from weasyprint import HTML
import docx

# Read the docx
doc = docx.Document(r'labwork\AI_Integration_VNTU.docx')

# Simple HTML conversion with proper Ukrainian support
html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { 
            font-family: Arial, sans-serif; 
            font-size: 14pt; 
            line-height: 1.5; 
            direction: rtl;
            text-align: right;
        }
        h1, h2, h3 { 
            text-align: center; 
            color: #333; 
            direction: ltr;
            text-align: left;
            margin: 20px 0 10px 0; 
        }
        p { 
            text-align: justify; 
            text-indent: 1.25cm;
            margin-bottom: 5px;
            direction: rtl;
            text-align: right;
        }
        .centered { 
            text-align: center; 
            direction: rtl;
            text-align: right;
        }
        .bold { font-weight: bold; }
        .italic { font-style: italic; }
        table { 
            border-collapse: collapse; 
            width: 100%; 
            margin: 15px 0; 
            direction: rtl;
            text-align: right;
        }
        th, td { 
            border: 1px solid #ddd; 
            padding: 8px; 
            text-align: right;
        }
        th { 
            background-color: #f2f2f2; 
        }
        ol { 
            margin-right: 1.25cm; 
            direction: rtl;
        }
        .references { 
            margin-top: 30px; 
            direction: rtl;
        }
    </style>
</head>
<body>"""

for p in doc.paragraphs:
    text = p.text.strip()
    if not text:
        continue
    
    alignment = p.alignment
    style_name = p.style.name
    bold = any(r.bold for r in p.runs)
    italic = any(r.italic for r in p.runs)
    
    if alignment == 1:  # CENTER
        if bold:
            html_content += '<p class="centered"><strong>' + text + '</strong></p>'
        else:
            html_content += '<p class="centered">' + text + '</p>'
    elif alignment == 3 and style_name == 'List Number':  # JUSTIFY list
        html_content += '<p>' + text + '</p>'
    elif alignment == 3:  # JUSTIFY
        html_content += '<p>' + text + '</p>'
    else:
        html_content += '<p>' + text + '</p>'

html_content += """</body></html>"""

# Write HTML and convert to PDF
with open(r'labwork\test_output.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print('HTML written, now converting to PDF...')
HTML(string=html_content, base_url=r'labwork/').write_pdf(r'labwork\AI_Integration_VNTU.pdf')
print('PDF created successfully!')