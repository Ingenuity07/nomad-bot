import jinja2
import logging
from resume.models import ResumeTemplate
from resume.latex_engine import escape_latex

logger = logging.getLogger(__name__)

class ResumeTemplateRenderer:
    """
    Renders structured resume specifications into compile-ready LaTeX using
    Jinja2 templates loaded dynamically from the database.
    """
    
    @staticmethod
    def render(template: ResumeTemplate, spec_data: dict) -> str:
        """Render the Jinja2 LaTeX source using the escaped structured specification data."""
        # Deep escape all string elements in the spec data to avoid LaTeX compilation errors
        escaped_data = ResumeTemplateRenderer._escape_data(spec_data)
        
        # Configure Jinja2 with custom delimiters to avoid conflicts with raw LaTeX brackets
        env = jinja2.Environment(
            block_start_string='((%',
            block_end_string='%))',
            variable_start_string='(((',
            variable_end_string=')))',
            comment_start_string='((#',
            comment_end_string='#))',
        )
        
        try:
            jinja_template = env.from_string(template.latex_source)
            rendered_latex = jinja_template.render(**escaped_data)
            return rendered_latex
        except Exception as e:
            logger.error(f"Failed to render LaTeX template {template.name}: {e}")
            raise RuntimeError(f"LaTeX rendering failed: {e}")

    @staticmethod
    def _escape_data(data: any) -> any:
        """Recursively traverse data structures and escape string components for LaTeX compatibility."""
        if isinstance(data, dict):
            return {k: ResumeTemplateRenderer._escape_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [ResumeTemplateRenderer._escape_data(i) for i in data]
        elif isinstance(data, str):
            return escape_latex(data)
        return data


DEFAULT_LATEX_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[margin=0.6in]{geometry}
\usepackage{hyperref}
\usepackage{titlesec}
\usepackage{enumitem}

\pagestyle{empty}
\setlist[itemize]{noitemsep, topsep=2pt, parsep=2pt, partopsep=0pt, leftmargin=15pt}

\titleformat{\section}{\large\bfseries\uppercase}{}{0em}{{\titrule[0.5pt]}\vspace{3pt}}[\vspace{2pt}]
\titleformat{\subsection}{\bfseries}{}{0em}{}[\vspace{1pt}]

\begin{document}

\begin{center}
  {\LARGE \bfseries ((( header.full_name )))}\\ \vspace{3pt}
  {\small ((( header.headline )))}\\ \vspace{2pt}
  {\small ((( header.email ))) | ((( header.phone ))) | ((( header.location )))}
\end{center}

\section*{Professional Summary}
((( summary )))

\section*{Technical Skills}
\begin{itemize}
((% for group in skills_groups %))
  \item \textbf{((( group.category ))):} ((( group.skills | join(', ') )))
((% endfor %))
\end{itemize}

\section*{Professional Experience}
((% for exp in experiences %))
\subsection*{((( exp.role ))) \hfill \normalfont{((( exp.start_date ))) -- ((( exp.end_date )))}}
\textit{((( exp.company )))} \hfill \textit{((( exp.location )))}
\begin{itemize}
((% for bullet in exp.bullet_points %))
  \item ((( bullet )))
((% endfor %))
\end{itemize}
((% endfor %))

\section*{Key Projects}
((% for proj in projects %))
\subsection*{((( proj.title )))}
\begin{itemize}
((% for bullet in proj.bullet_points %))
  \item ((( bullet )))
((% endfor %))
\end{itemize}
((% endfor %))

\end{document}
"""

def get_or_create_default_template() -> ResumeTemplate:
    """Helper function to load or seed the default LaTeX resume template in the database."""
    template, _ = ResumeTemplate.objects.get_or_create(
        name="modern",
        defaults={
            "latex_source": DEFAULT_LATEX_TEMPLATE,
            "supported_sections": ["summary", "skills", "experience", "projects"],
            "variables": {}
        }
    )
    return template

