class MarkdownResumeRenderer:
    """Compiles a Structured Resume Specification into clean GitHub Flavored Markdown."""

    @staticmethod
    def render(spec_data: dict) -> str:
        """Render the structured resume data as Markdown."""
        header = spec_data.get("header", {})
        summary = spec_data.get("summary", "")
        skills_groups = spec_data.get("skills_groups", [])
        experiences = spec_data.get("experiences", [])
        projects = spec_data.get("projects", [])

        md = []
        md.append(f"# {header.get('full_name', 'Shivam Singh')}")
        md.append(f"**{header.get('headline', 'Senior Software Engineer')}**\n")
        
        contacts = []
        if header.get("email"): contacts.append(f"Email: {header['email']}")
        if header.get("phone"): contacts.append(f"Phone: {header['phone']}")
        if header.get("location"): contacts.append(f"Location: {header['location']}")
        
        md.append(" | ".join(contacts) + "\n")
        
        # Summary
        md.append("## Professional Summary")
        md.append(summary + "\n")
        
        # Skills
        md.append("## Technical Skills")
        for g in skills_groups:
            md.append(f"*   **{g.get('category', 'Skills')}:** {', '.join(g.get('skills', []))}")
        md.append("")

        # Experience
        md.append("## Professional Experience")
        for exp in experiences:
            md.append(f"### {exp.get('role')} at {exp.get('company')} ({exp.get('start_date')} - {exp.get('end_date')})")
            for bullet in exp.get("bullet_points", []):
                md.append(f"*   {bullet}")
            md.append("")
            
        # Projects
        md.append("## Key Projects")
        for proj in projects:
            md.append(f"### {proj.get('title', 'Project')}")
            for bullet in proj.get("bullet_points", []):
                md.append(f"*   {bullet}")
            md.append("")

        return "\n".join(md)
