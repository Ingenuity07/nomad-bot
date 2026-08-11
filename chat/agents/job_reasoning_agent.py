from .research_agent import ResearchAgent

class JobReasoningAgent(ResearchAgent):
    """
    Specialized agent to analyze job descriptions from web pages,
    compare them against the user's resume, identify skill gaps,
    and customize the resume to highlight matching skills.
    """
    
    @property
    def name(self) -> str:
        return "JobReasoningAgent"
        
    @property
    def system_prompt(self) -> str:
        return (
            "You are a Job Reasoning Agent. Your goal is to help the user analyze job descriptions "
            "and tailor their resume (stored in Markdown or text format) to match the job.\n\n"
            "Workflow:\n"
            "1. Read the job description from a web page using the `browser_action` tool (action: 'navigate' then 'get_content').\n"
            "2. Read the user's base resume from a local file (using `read_file`) or from their GitHub repository (using `github_read_file`).\n"
            "3. Identify skill/qualification gaps between the resume and the job description.\n"
            "4. Tailor the resume honestly—rephrase descriptions, highlight relevant projects, and prioritize matching skills. "
            "Do NOT fabricate false experience or credentials.\n"
            "5. Save the tailored resume either locally (using file tools) or directly commit it to a feature branch on the user's repository (using `github_write_file`).\n"
            "6. Output a final report containing:\n"
            "   - Match Score (0 to 100%)\n"
            "   - Skills/Qualifications Gaps Identified\n"
            "   - Key changes made to the resume\n"
            "   - Details on where the customized resume was saved/committed\n\n"
            "To use a tool, return a JSON object with 'tool_name' and 'tool_args'. "
            "When you have completed all tasks, return your final answer in a JSON object with 'response'."
        )

    def execute(self, prompt: str, conversation_history: list = None, **kwargs) -> str:
        user_profile_data = kwargs.get("user_profile_data", {})
        if user_profile_data:
            profile_text = (
                "\n\nUser Profile Information (to use when filling job application forms):\n"
                f"- Full Name: {user_profile_data.get('full_name')}\n"
                f"- Email: {user_profile_data.get('email')}\n"
                f"- Phone: {user_profile_data.get('phone')}\n"
                f"- LinkedIn: {user_profile_data.get('linkedin_url')}\n"
                f"- GitHub: {user_profile_data.get('github_url')}\n"
                f"- Portfolio: {user_profile_data.get('portfolio_url')}\n"
            )
            prompt += profile_text
            
        return super().execute(prompt, conversation_history, **kwargs)
