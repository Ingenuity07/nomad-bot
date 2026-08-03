class HTMLResumeRenderer:
    """Compiles a Structured Resume Specification into a beautiful, responsive HTML page."""

    @staticmethod
    def render(spec_data: dict) -> str:
        header = spec_data.get("header", {})
        summary = spec_data.get("summary", "")
        skills_groups = spec_data.get("skills_groups", [])
        experiences = spec_data.get("experiences", [])
        projects = spec_data.get("projects", [])

        # Build contact list
        contacts = []
        if header.get("email"):
            contacts.append(f"<span>Email: {header['email']}</span>")
        if header.get("phone"):
            contacts.append(f"<span>Phone: {header['phone']}</span>")
        if header.get("location"):
            contacts.append(f"<span>Location: {header['location']}</span>")
        
        contact_line = " • ".join(contacts)

        # Build skills list
        skills_html = ""
        for g in skills_groups:
            skills_html += f"""
            <div class="skill-group">
                <strong>{g.get("category", "")}:</strong> {", ".join(g.get("skills", []))}
            </div>"""

        # Build experiences
        exp_html = ""
        for exp in experiences:
            bullets = "".join([f"<li>{b}</li>" for b in exp.get("bullet_points", [])])
            exp_html += f"""
            <div class="job-block">
                <div class="job-header">
                    <strong>{exp.get("role")}</strong> at <em>{exp.get("company")}</em>
                    <span class="dates">{exp.get("start_date")} - {exp.get("end_date")}</span>
                </div>
                <ul>{bullets}</ul>
            </div>"""

        # Build projects
        proj_html = ""
        for proj in projects:
            bullets = "".join([f"<li>{b}</li>" for b in proj.get("bullet_points", [])])
            proj_html += f"""
            <div class="project-block">
                <strong>{proj.get("title")}</strong>
                <ul>{bullets}</ul>
            </div>"""

        html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{header.get("full_name")} - Resume</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            color: #334155;
            line-height: 1.5;
            max-width: 800px;
            margin: 40px auto;
            padding: 0 20px;
        }}
        h1 {{
            margin-bottom: 5px;
            color: #0f172a;
        }}
        .headline {{
            font-size: 1.1rem;
            color: #6366f1;
            font-weight: 600;
            margin-bottom: 8px;
        }}
        .contact {{
            font-size: 0.85rem;
            color: #64748b;
            margin-bottom: 20px;
        }}
        section {{
            margin-top: 24px;
            border-top: 1px solid #e2e8f0;
            padding-top: 12px;
        }}
        h2 {{
            font-size: 1.1rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #0f172a;
            margin-bottom: 12px;
        }}
        .job-block, .project-block {{
            margin-bottom: 16px;
        }}
        .job-header {{
            display: flex;
            justify-content: space-between;
            font-size: 0.95rem;
            margin-bottom: 4px;
        }}
        .dates {{
            color: #64748b;
            font-size: 0.85rem;
        }}
        ul {{
            margin: 5px 0;
            padding-left: 20px;
            font-size: 0.9rem;
        }}
        li {{
            margin-bottom: 4px;
        }}
    </style>
</head>
<body>
    <h1>{header.get("full_name")}</h1>
    <div class="headline">{header.get("headline")}</div>
    <div class="contact">{contact_line}</div>

    <section>
        <h2>Summary</h2>
        <p style="font-size: 0.95rem;">{summary}</p>
    </section>

    <section>
        <h2>Technical Skills</h2>
        {skills_html}
    </section>

    <section>
        <h2>Experience</h2>
        {exp_html}
    </section>

    <section>
        <h2>Projects</h2>
        {proj_html}
    </section>
</body>
</html>
"""
        return html_template
