# Here the pipeline of execution

## Step 0

In this step the uesr just choose which template he wants to create like lab1-template.py or lab2-teamplate.py, according to the choice of user must appear the set of blanks to fill, there are only two sectoin of gaps the user can fill independetly to template choice:

1) Special parametres
2) Universal parametres

### Special parametres

The special paramters is a set of gaps, which is specific to each template, for example, in the lab1-teamplate.py we have following code:


```python

import os
import docx
from docx.shared import Pt
from docx.shared import Cm as DocxCm  # Унікальна назва для відступів у Word
from docx.enum.text import WD_ALIGN_PARAGRAPH

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.units import cm as PdfCm  # Унікальна назва для відступів у PDF

def create_docx(filename="Lab_Template_Final.docx"):
    """Генерує DOCX файл з ідеальним форматуванням (Times New Roman, 14pt, інтервал 1.5)"""
    doc = docx.Document()
    
    # Налаштування стилю "Normal" для всього документа
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(14)
    
    # Налаштування інтервалу та вирівнювання за замовчуванням
    paragraph_format = style.paragraph_format
    paragraph_format.line_spacing = 1.5
    paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    paragraph_format.first_line_indent = DocxCm(1.25) # Червоний рядок
    paragraph_format.space_after = Pt(0)

    # 1. Заголовок "ЛАБОРАТОРНА РОБОТА" (по центру, звичайний)
    p_header = doc.add_paragraph()
    p_header.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_header.paragraph_format.first_line_indent = DocxCm(0)
    p_header.add_run("ЛАБОРАТОРНА РОБОТА № [ВСТАВТЕ НОМЕР]")

    # 2. Назва роботи (по центру, жирний)
    p_title = doc.add_paragraph()
    p_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.first_line_indent = DocxCm(0)
    run_title = p_title.add_run("[Вставте назву лабораторної роботи]")
    run_title.bold = True
    
    doc.add_paragraph() # Порожній рядок

    # 3. Мета роботи (з абзацу, "Мета роботи" - жирним)
    p_meta = doc.add_paragraph()
    run_meta = p_meta.add_run("Мета роботи")
    run_meta.bold = True
    p_meta.add_run(" - [вставте текст мети роботи]")

    doc.add_paragraph()

    # 4. Загальні відомості (по центру, жирний)
    p_zagalni = doc.add_paragraph()
    p_zagalni.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_zagalni.paragraph_format.first_line_indent = DocxCm(0)
    p_zagalni.add_run("Загальні відомості").bold = True

    # Текст загальних відомостей (з абзацу, по ширині)
    doc.add_paragraph("[Вставте теоретичний матеріал тут. Абзацний відступ та вирівнювання по ширині налаштовані автоматично.]")
    
    doc.add_paragraph()

    # 5. Завдання. (по центру, жирний)
    p_zavd = doc.add_paragraph()
    p_zavd.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_zavd.paragraph_format.first_line_indent = DocxCm(0)
    p_zavd.add_run("Завдання.").bold = True

    # Пункти завдання (з абзацу)
    doc.add_paragraph("1. [Вставте перше завдання]")
    doc.add_paragraph("2. [Вставте друге завдання]")

    doc.add_paragraph()

    # 6. Контрольні запитання (по центру, жирний)
    p_kontr = doc.add_paragraph()
    p_kontr.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_kontr.paragraph_format.first_line_indent = DocxCm(0)
    p_kontr.add_run("Контрольні запитання").bold = True

    doc.add_paragraph("1. [Вставте перше запитання]")
    doc.add_paragraph("2. [Вставте друге запитання]")

    doc.add_paragraph()

    # 7. Література (по центру, жирний)
    p_lit = doc.add_paragraph()
    p_lit.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_lit.paragraph_format.first_line_indent = DocxCm(0)
    p_lit.add_run("Література").bold = True

    doc.add_paragraph("1. [Вставте перше джерело]")
    doc.add_paragraph("2. [Вставте друге джерело]")

    doc.save(filename)
    print(f"Успішно створено файл: {filename}")


def create_pdf(filename="Lab_Template_Final.pdf"):
    """Генерує PDF з точним дотриманням відступів та шрифтів"""
    
    # Реєстрація шрифтів Times New Roman (стандартні шляхи для Windows)
    try:
        pdfmetrics.registerFont(TTFont('TimesNewRoman', 'times.ttf'))
        pdfmetrics.registerFont(TTFont('TimesNewRoman-Bold', 'timesbd.ttf'))
        font_regular = 'TimesNewRoman'
        font_bold = 'TimesNewRoman-Bold'
    except Exception as e:
        print("Попередження: Шрифти Times New Roman не знайдено в системі. Використовується шрифт за замовчуванням.")
        font_regular = 'Helvetica'
        font_bold = 'Helvetica-Bold'

    # Використовуємо PdfCm для безпечного встановлення полів
    doc = SimpleDocTemplate(filename, pagesize=A4,
                            rightMargin=1.5*PdfCm, leftMargin=3.0*PdfCm,
                            topMargin=2.0*PdfCm, bottomMargin=2.0*PdfCm)
    
    # Налаштування стилів (14pt, 1.5 інтервал (leading ~21))
    style_center = ParagraphStyle(
        name='CenterNormal', fontName=font_regular, fontSize=14, leading=21, 
        alignment=TA_CENTER, spaceAfter=14)
    
    style_center_bold = ParagraphStyle(
        name='CenterBold', fontName=font_bold, fontSize=14, leading=21, 
        alignment=TA_CENTER, spaceAfter=14, spaceBefore=14)
    
    style_body = ParagraphStyle(
        name='BodyJustify', fontName=font_regular, fontSize=14, leading=21, 
        alignment=TA_JUSTIFY, firstLineIndent=35, spaceAfter=10) # 35 point ~ 1.25 cm

    story = []

    # 1. Заголовок
    story.append(Paragraph("ЛАБОРАТОРНА РОБОТА № [ВСТАВТЕ НОМЕР]", style_center))
    # 2. Назва
    story.append(Paragraph("[Вставте назву лабораторної роботи]", style_center_bold))
    
    # 3. Мета
    story.append(Paragraph("<b>Мета роботи</b> - [вставте текст мети роботи]", style_body))
    
    # 4. Загальні відомості
    story.append(Paragraph("Загальні відомості", style_center_bold))
    story.append(Paragraph("[Вставте теоретичний матеріал тут. Абзацний відступ та вирівнювання по ширині налаштовані автоматично.]", style_body))
    
    # 5. Завдання
    story.append(Paragraph("Завдання.", style_center_bold))
    story.append(Paragraph("1. [Вставте перше завдання]", style_body))
    story.append(Paragraph("2. [Вставте друге завдання]", style_body))
    
    # 6. Контрольні запитання
    story.append(Paragraph("Контрольні запитання", style_center_bold))
    story.append(Paragraph("1. [Вставте перше запитання]", style_body))
    story.append(Paragraph("2. [Вставте друге запитання]", style_body))

    # 7. Література
    story.append(Paragraph("Література", style_center_bold))
    story.append(Paragraph("1. [Вставте перше джерело]", style_body))
    story.append(Paragraph("2. [Вставте друге джерело]", style_body))

    doc.build(story)
    print(f"Успішно створено файл: {filename}")

if __name__ == "__main__":
    create_docx("Lab_Template_Final.docx")
    create_pdf("Lab_Template_Final.pdf")




```


Where we have following sections:

- заголовок
- назва
- мета
- загальні відомості 
- завдання
- контрольні питання
- література


So we have an option to fill the gaps like:

 ![alt text](image.png)

But if the template is different thats why it is dynamic set of instruions, espeically for eaach template, it can changes from templte to template, each gap from the list will have an option like:

**GIVE ACCESS TO AI** - if it true, the ai can change the content of thi app, if it is false, AI cant do it.




### Universal parametres

Universal parameteres, is a set of paratemres which is static for each template and it looks like:

![alt text](image-1.png)

![alt text](image-2.png)

This paramters will be always availabel to user, not depend`s of user template choice, here more about this:

- User preferences ( the text, that must be used while model geneeating content, it must be the static option for each template user choose, all other fields can be different consider the template script)
 Also user can manage some parameters, which also must be static for all templates, here these parameters:
 - the number of text like ( short, middle, long, large) also each state contain the range number of words, like the short is 500 - 1000, middle 1000- 1700, long 1700 - 2500, large 2500+. Also, the additional option to range this number of words more accurately.
- if the work contain images or shemas ( if it turns on, the text LLM model can crate an image reference inside the content, if it necessary, and then the image model will crate an image according to this references, and insert into the final PDF and docx version). In what format this references UST be, and how models must understand each other must be written inside the global instructions.
- also it must have the modes, also parameter which control the hardness off the text ( like for schoolers, graduated, 1 grade if university, 2 grade of university and so on, up to bakalavr)
- the user attached files, if the user attach file it also must be as a static paramter like: ![alt text](image-3.png)
- must be a checksquear to enable speacial instrucions (it is an md file, which describe very detailed each template for AI, it must be stored in the app/instructions/template-ins/ if the user turn on it, the instucions added to the context to folowing step)
- general instrucions ( the defualt instrucions which alsways must be added to the context to next step)
- the option to add user style file as a context to the next step (it is a set of instrucions which contains the infromation to the output considered the user prefernces, the style, how to write and other options the user want to see)

And also the user prompt itself, which must be the main prompt to hte model and must create the main part of conent in future doc, according to this promt and parateters above must be create conetn to fill py scirpt( *but if the user turn off the AI ACCESS to curret gap, the AI must just keep content and do not change it*).


### Json request


The json from this user input must be divided into two parts, or divided into two files like in one file/part will be the dynamic information, whihc will be determinned by template choise, there will be all content that use fill in gaps for this template. IN second part/file must be all static informatoin with the main prompt, set of parameters, and infromation about the attached files 



## Step 1 (debug/transit)


First step is a step afte rthe user put information, fill all gaps, attache all filse, configre the user_style.md file in GUI and other, and just in moment user clikc create.

Before any actions, in the app/debug/transit must be putted following files and jsons:

1) All from the step 0, the jsons and files that is mention in the step 0.
2) All files which were added as a context in the pdf, docx, pptx, image formats must be converted to the txt and putted to hte transit folder (one file, one txt), using the existed scirpts like:

- [docx](../../docx2txt.py)

- [image](../../image2txt.py)

- [pfd](../../pdf2txt.py)

- [pptx](../../pptx2txt.py)


## Step 2 (debug/compact/) - afte the compress 


This step must display files from the step 1 but, some of this files have gone through the compact model qwen, for more detiald explanation of this part of step check the backend work.md and the BACKEND_PLAN.md, thre you can find some details how it msut be, but pay attention only on this part, wihout reading other detials of those files, beacue it not more relevant or us.

    IN other words, the local model must take all txt files from the attachd files, and the special instrucions if it was added, and compact it save the main informaion but reduce the number of tokens of this files, after this each compacted file must be saved in the app/data/cache as a cach txt files and putted in the database and if user will attache the same file again, it wil not compact it again, but just grab the existing from the cache, also must be configure the period of removing those cache in the setting of the app (also it must be in the envirmonet vairables). The same with special instruions, but now without period of removing.

**About the env.** also must be the variable that control the history of session, and also remove the old session after some period of time, it must be controls in the .env

The files like global inst, user style, and the static content except attache files must be saved as it is.

**importatn** the model must be active only during the compacting, then it must be not active. Also it must be preinstalled with whole app. 

## step 3 (debug/main_out/ ) - afte the main LLM model

This step must display the state of files just afte the main model, it must disply the fileld.py scritp, the session json infor all those files, but alread without the context from attahed files, and without the speacial for template instruiosn. 

Also must be create an manifest.json where will be putted the instruions of creations diagrams (in a case, if the image generate was truned on).

**Important** the global instrions still must be here as a part of context to the image-generaor model


## step 4 (debug/image-gen ) - afte the image-generator model (optional)

If the user enabel the images in the work, so here must be already created images instead of manifest.json and the rest files from the previous step.


## step 5 (debug/output ) - final

In this step we must have the fuully configured docx file, pdf file, the last version of the filled python scirpt, images from the prevous step. And fully fileed index.json with all information about the session, stattime, endtime, compact time, name and other.