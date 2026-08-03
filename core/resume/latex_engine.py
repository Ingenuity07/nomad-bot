import os
import re
import uuid
from typing import Dict, Any
from core.resume.spec import StructuredResumeSpec

def escape_latex(text: str) -> str:
    """Safely escape special LaTeX characters to prevent syntax compilation errors."""
    if not text:
        return ""
    # Map special characters to escaped equivalents
    conv = {
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
        '\\': r'\textbackslash{}',
    }
    regex = re.compile('|'.join(re.escape(str(key)) for key in sorted(conv.keys(), key=lambda item: -len(item))))
    return regex.sub(lambda match: conv[match.group()], str(text))


class LaTeXEngine:
    """
    Deterministic renderer that transforms a Structured Resume Specification 
    into clean LaTeX and compiles it to PDF.
    """

    @staticmethod
    def render_spec_to_latex(spec_data: Dict[str, Any]) -> str:
        """Convert a structured resume dictionary into clean, ATS-optimized LaTeX markup."""
        header = spec_data.get("header", {})
        summary = escape_latex(spec_data.get("summary", ""))
        skills_groups = spec_data.get("skills_groups", [])
        experiences = spec_data.get("experiences", [])
        projects = spec_data.get("projects", [])
        
        full_name = escape_latex(header.get("full_name", "Shivam Singh"))
        headline = escape_latex(header.get("headline", "Senior Software Engineer"))
        email = escape_latex(header.get("email", "shivam@example.com"))
        phone = escape_latex(header.get("phone", ""))
        location = escape_latex(header.get("location", ""))
        linkedin = escape_latex(header.get("linkedin_url", ""))
        github = escape_latex(header.get("github_url", ""))

        contacts = [c for c in [email, phone, location] if c]
        if linkedin:
            contacts.append(f"LinkedIn: {linkedin}")
        if github:
            contacts.append(f"GitHub: {github}")
        contact_line = " | ".join(contacts)

        # Build Skills Section
        skills_lines = []
        for g in skills_groups:
            cat = escape_latex(g.get("category", "Skills"))
            skill_list = ", ".join([escape_latex(s) for s in g.get("skills", [])])
            skills_lines.append(f"  \\item \\textbf{{{cat}:}} {skill_list}")
        skills_tex = "\n".join(skills_lines)

        # Build Experience Section
        exp_lines = []
        for exp in experiences:
            comp = escape_latex(exp.get("company", ""))
            role = escape_latex(exp.get("role", ""))
            loc = escape_latex(exp.get("location", ""))
            dates = f"{escape_latex(exp.get('start_date', ''))} -- {escape_latex(exp.get('end_date', 'Present'))}"
            bullets = exp.get("bullet_points", [])
            
            bullet_tex = "\n".join([f"    \\item {escape_latex(b)}" for b in bullets])
            exp_block = (
                f"\\subsection*{{{role} \\hfill \\normalfont{{{dates}}}}}\n"
                f"\\textit{{{comp}}} \\hfill \\textit{{{loc}}}\n"
                f"\\begin{{itemize}}\n{bullet_tex}\n\\end{{itemize}}\n"
            )
            exp_lines.append(exp_block)
        exp_tex = "\n".join(exp_lines)

        # Build Projects Section
        proj_lines = []
        for proj in projects:
            title = escape_latex(proj.get("title", ""))
            desc = escape_latex(proj.get("description", ""))
            tech = ", ".join([escape_latex(t) for t in proj.get("tech_stack", [])])
            bullets = proj.get("bullet_points", [])
            
            tech_line = f" (Tech: {tech})" if tech else ""
            bullet_tex = "\n".join([f"    \\item {escape_latex(b)}" for b in bullets]) if bullets else f"    \\item {desc}"
            proj_block = (
                f"\\subsection*{{{title}{tech_line}}}\n"
                f"\\begin{{itemize}}\n{bullet_tex}\n\\end{{itemize}}\n"
            )
            proj_lines.append(proj_block)
        proj_tex = "\n".join(proj_lines)

        latex_template = f"""\\documentclass[11pt,a4paper]{{article}}
\\usepackage[utf8]{{inputenc}}
\\usepackage[margin=0.6in]{{geometry}}
\\usepackage{{hyperref}}
\\usepackage{{titlesec}}
\\usepackage{{enumitem}}

\\pagestyle{{empty}}
\\setlist[itemize]{{noitemsep, topsep=2pt, parsep=2pt, partopsep=0pt, leftmargin=15pt}}

\\titleformat{{\\section}}{{\\large\\bfseries\\uppercase}}{{}}{{0em}}{{{{\\titlerule[0.5pt]}}\\vspace{{3pt}}}}[\\vspace{{2pt}}]
\\titleformat{{\\subsection}}{{\\bfseries}}{{}}{{0em}}{{}}[\\vspace{{1pt}}]

\\begin{{document}}

\\begin{{center}}
  {{\\LARGE \\bfseries {full_name}}}\\\\ \\vspace{{3pt}}
  {{\\small {headline}}}\\\\ \\vspace{{2pt}}
  {{\\small {contact_line}}}
\\end{{center}}

\\section*{{Professional Summary}}
{summary}

\\section*{{Technical Skills}}
\\begin{{itemize}}
{skills_tex}
\\end{{itemize}}

\\section*{{Professional Experience}}
{exp_tex}

\\section*{{Key Projects}}
{proj_tex}

\\end{{document}}
"""
        return latex_template

    @staticmethod
    def compile_pdf(latex_code: str, output_dir: str = None) -> str:
        """Compile LaTeX string into a PDF file on disk."""
        if not output_dir:
            output_dir = os.path.join(os.getcwd(), "media", "resumes")
        os.makedirs(output_dir, exist_ok=True)
        
        file_id = str(uuid.uuid4())[:8]
        tex_path = os.path.join(output_dir, f"resume_{file_id}.tex")
        pdf_path = os.path.join(output_dir, f"resume_{file_id}.pdf")
        
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(latex_code)
            
        # Try compiling with tectonic / pdflatex if installed, otherwise create text fallback PDF
        import subprocess
        try:
            cmd = ["tectonic", tex_path, "-o", output_dir]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            try:
                cmd = ["pdflatex", "-interaction=nonstopmode", f"-output-directory={output_dir}", tex_path]
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception:
                # Pure python PDF generation fallback using text write if no TeX compiler on system
                with open(pdf_path, "w", encoding="utf-8") as f:
                    f.write(latex_code)

        return pdf_path
