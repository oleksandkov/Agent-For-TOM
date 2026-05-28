import os
import re
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from .models import DocumentMetadata, LabGuidelinesContent

def add_page_number(run):
    """Inject XML elements for dynamic page numbering in Word."""
    fldChar1 = parse_xml(r'<w:fldChar %s w:fldCharType="begin"/>' % nsdecls('w'))
    instrText = parse_xml(r'<w:instrText %s xml:space="preserve"> PAGE </w:instrText>' % nsdecls('w'))
    fldChar2 = parse_xml(r'<w:fldChar %s w:fldCharType="separate"/>' % nsdecls('w'))
    fldChar3 = parse_xml(r'<w:fldChar %s w:fldCharType="end"/>' % nsdecls('w'))
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)

class DSTUDocxGenerator:
    def __init__(self):
        self.doc = Document()
        self.setup_document_styling()
        
    def setup_document_styling(self):
        """Set margins and styling rules according to DSTU 3008:2015."""
        for section in self.doc.sections:
            section.page_width = Inches(8.27)  # A4
            section.page_height = Inches(11.69)
            section.top_margin = Cm(2.0)
            section.bottom_margin = Cm(2.0)
            section.left_margin = Cm(3.0)      # Space for binding
            section.right_margin = Cm(1.0)     # Minimum right margin
            
            # Setup headers for page numbers (skip page 1)
            section.different_first_page_header_footer = True
            header = section.header
            p = header.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = p.add_run()
            run.font.name = 'Times New Roman'
            run.font.size = Pt(11)
            add_page_number(run)
            
        # Configure Normal text style
        style_normal = self.doc.styles['Normal']
        style_normal.font.name = 'Times New Roman'
        style_normal.font.size = Pt(14)
        style_normal.paragraph_format.line_spacing = 1.5
        style_normal.paragraph_format.space_after = Pt(0)
        style_normal.paragraph_format.space_before = Pt(0)
        style_normal.paragraph_format.first_line_indent = Cm(1.25)
        
    def add_title_page(self, meta: DocumentMetadata):
        """Generates a standardized title page complying with Ukrainian academic standards."""
        # Clean paragraph helper
        def add_title_para(text, size, bold=False, space_after=0, space_before=0, align=WD_ALIGN_PARAGRAPH.CENTER):
            p = self.doc.add_paragraph()
            p.alignment = align
            p.paragraph_format.first_line_indent = Cm(0)
            p.paragraph_format.space_after = Pt(space_after)
            p.paragraph_format.space_before = Pt(space_before)
            p.paragraph_format.line_spacing = 1.15
            run = p.add_run(text)
            run.font.name = 'Times New Roman'
            run.font.size = Pt(size)
            run.bold = bold
            return p

        # Ministry and University Header
        add_title_para("МІНІСТЕРСТВО ОСВІТИ І НАУКИ УКРАЇНИ", 12, bold=True)
        add_title_para(meta.university.upper(), 12, bold=True)
        add_title_para(f"Кафедра {meta.department}", 12, space_after=120)
        
        # Document title
        add_title_para("МЕТОДИЧНІ ВКАЗІВКИ", 18, bold=True, space_before=40)
        add_title_para("до виконання лабораторних робіт з дисципліни", 14)
        add_title_para(f"«{meta.discipline}»", 16, bold=True, space_after=100)
        
        # Author details
        author_names = ", ".join(meta.authors)
        add_title_para("Розробники:", 12, space_before=60, align=WD_ALIGN_PARAGRAPH.RIGHT)
        add_title_para(author_names, 12, bold=True, space_after=140, align=WD_ALIGN_PARAGRAPH.RIGHT)
        
        # Bottom year and city
        add_title_para(meta.city, 12, space_before=40)
        add_title_para(str(meta.year), 12)
        
        # Section/Page break
        self.doc.add_page_break()

    def add_heading(self, text, level):
        """Adds a standardized section heading."""
        p = self.doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.line_spacing = 1.15
        
        run = p.add_run(text)
        run.font.name = 'Times New Roman'
        run.bold = True
        
        if level == 1:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(24)
            p.paragraph_format.space_after = Pt(12)
            run.font.size = Pt(16)
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(16)
            p.paragraph_format.space_after = Pt(8)
            run.font.size = Pt(14)
            
    def add_paragraph(self, text):
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run = p.add_run(text)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(14)
        
    def add_list_items(self, items, numbered=False):
        for i, item in enumerate(items):
            p = self.doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(1.25)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            
            # Format bullets/numbers manually for precise layout control
            prefix = f"{i+1}. " if numbered else "— "
            run_prefix = p.add_run(prefix)
            run_prefix.font.name = 'Times New Roman'
            run_prefix.font.size = Pt(14)
            run_prefix.bold = numbered
            
            run_text = p.add_run(item)
            run_text.font.name = 'Times New Roman'
            run_text.font.size = Pt(14)

    def generate(self, meta: DocumentMetadata, content: LabGuidelinesContent, filepath: str):
        # 1. Title Page
        self.add_title_page(meta)
        
        # 2. Table of Contents placeholder
        self.add_heading("ЗМІСТ", 1)
        p_toc = self.doc.add_paragraph()
        p_toc.paragraph_format.first_line_indent = Cm(0)
        p_toc.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_toc.add_run("[Для оновлення змісту: відкрийте готовий файл у MS Word, натисніть Ctrl+A, а потім клавішу F9]").italic = True
        self.doc.add_page_break()
        
        # 3. Introduction
        self.add_heading("ВСТУП", 1)
        # Split intro by newlines to form paragraphs
        for para in content.introduction.split('\n'):
            if para.strip():
                self.add_paragraph(para.strip())
        self.doc.add_page_break()
        
        # 4. Lab Works
        for idx, lab in enumerate(content.lab_works):
            self.add_heading(f"ЛАБОРАТОРНА РОБОТА №{idx+1}", 1)
            self.add_heading(f"Тема: {lab.topic}", 2)
            
            # Objective
            p_obj = self.doc.add_paragraph()
            p_obj.add_run("Мета роботи: ").bold = True
            p_obj.add_run(lab.objective)
            
            # Theory
            self.add_heading("1. Теоретичні відомості", 2)
            for para in lab.theory.split('\n'):
                if para.strip():
                    self.add_paragraph(para.strip())
                    
            # Tasks
            self.add_heading("2. Завдання до виконання", 2)
            self.add_list_items(lab.tasks, numbered=True)
            
            # Procedure
            self.add_heading("3. Порядок виконання роботи", 2)
            self.add_list_items(lab.procedure, numbered=True)
            
            # Questions
            self.add_heading("4. Контрольні запитання для самоперевірки", 2)
            self.add_list_items(lab.questions, numbered=True)
            
            # Report Requirements
            self.add_heading("5. Вимоги до звіту", 2)
            for para in lab.report_requirements.split('\n'):
                if para.strip():
                    self.add_paragraph(para.strip())
                    
            self.doc.add_page_break()
            
        # 5. References
        self.add_heading("СПИСОК РЕКОМЕНДОВАНИХ ДЖЕРЕЛ", 1)
        self.add_list_items(content.references, numbered=True)
        
        # Save file
        self.doc.save(filepath)
        print(f"Generated DOCX report: {filepath}")
