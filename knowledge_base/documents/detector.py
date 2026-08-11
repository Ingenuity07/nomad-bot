import re
from typing import Dict

class SectionDetector:
    """Segments raw document text into semantic sections based on common headings."""
    
    @staticmethod
    def detect_sections(text: str) -> Dict[str, str]:
        sections = {
            "summary": "",
            "experience": "",
            "projects": "",
            "skills": "",
            "education": ""
        }
        
        # Standard heading patterns
        patterns = {
            "summary": re.compile(r'\b(summary|profile|objective|about me)\b', re.IGNORECASE),
            "experience": re.compile(r'\b(experience|work history|employment|career)\b', re.IGNORECASE),
            "projects": re.compile(r'\b(projects|key projects|academic projects)\b', re.IGNORECASE),
            "skills": re.compile(r'\b(skills|technical skills|expertise|technologies)\b', re.IGNORECASE),
            "education": re.compile(r'\b(education|academic background)\b', re.IGNORECASE)
        }
        
        lines = text.split("\n")
        current_section = "summary"  # default starting block
        
        section_lines = {k: [] for k in sections.keys()}
        
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue
                
            # Check if this line matches a heading pattern (heading lines are typically short)
            found_heading = False
            if len(line_strip) < 30:
                for sec_name, pattern in patterns.items():
                    if pattern.search(line_strip):
                        current_section = sec_name
                        found_heading = True
                        break
            
            if not found_heading:
                section_lines[current_section].append(line)
                
        for k in sections.keys():
            sections[k] = "\n".join(section_lines[k]).strip()
            
        return sections
