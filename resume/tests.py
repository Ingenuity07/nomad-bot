from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient
from applications.models import ApplicationTracker
from knowledge_base.models import UserProfile, ProfessionalKnowledgeBase, Experience, Project, Skill, JobPosting
from resume.models import ResumeVersion, ATSReport
from resume.latex_engine import LaTeXEngine, escape_latex
from knowledge_base.jobs.ats_analyzer import ATSGapAnalyzer


class NomadV3EngineTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, _ = UserProfile.objects.get_or_create(
            username='default_user',
            defaults={'email': 'default@example.com', 'full_name': 'Shivam Singh'}
        )
        self.kb = ProfessionalKnowledgeBase.objects.create(
            user_profile=self.user,
            title="Senior Software Engineer",
            headline="Full-stack AI Systems Engineer",
            summary="Experienced in Python, Django, PostgreSQL, and LLM Orchestration.",
            years_of_experience=6
        )
        self.exp = Experience.objects.create(
            user_profile=self.user,
            company="Ridecell",
            role="Senior Software Engineer",
            location="San Francisco, CA",
            start_date="Jan 2022",
            end_date="Present",
            bullet_points=[
                "Architected real-time telemetry processing microservices handling 10k req/sec.",
                "Reduced deployment latency by 40% using automated CI/CD pipelines."
            ],
            tech_stack=["Python", "Django", "Redis", "Docker"]
        )
        self.skill1 = Skill.objects.create(user_profile=self.user, category="languages", name="Python", proficiency="Expert")
        self.skill2 = Skill.objects.create(user_profile=self.user, category="frameworks", name="Django", proficiency="Expert")

    def test_latex_escape_and_deterministic_rendering(self):
        # Verify safe LaTeX escaping
        raw_str = "C++ & Python (100% #1 $50k) {test} _var_"
        escaped = escape_latex(raw_str)
        self.assertIn(r"\&", escaped)
        self.assertIn(r"\%", escaped)
        self.assertIn(r"\#", escaped)
        self.assertIn(r"\$", escaped)
        self.assertIn(r"\_", escaped)

        spec = {
            "header": {
                "full_name": "Shivam Singh",
                "headline": "Senior Software Engineer",
                "email": "shivam@example.com"
            },
            "summary": "Building scalable backend services.",
            "skills_groups": [{"category": "Languages", "skills": ["Python", "Django"]}],
            "experiences": [{
                "company": "Ridecell", "role": "Senior Engineer", "start_date": "2022", "end_date": "Present",
                "bullet_points": ["Architected telematics pipeline handling 10k req/sec"]
            }],
            "projects": []
        }
        latex_code = LaTeXEngine.render_spec_to_latex(spec)
        self.assertIn(r"\begin{document}", latex_code)
        self.assertIn("Shivam Singh", latex_code)
        self.assertIn("Ridecell", latex_code)

    def test_ats_gap_analyzer(self):
        job = JobPosting.objects.create(
            user_profile=self.user,
            company_name="Google",
            job_title="Senior AI Infrastructure Engineer",
            raw_text="Looking for a Python and Django expert with Redis and Kubernetes experience.",
            required_skills=["Python", "Django", "Redis"],
            preferred_skills=["Kubernetes"],
            ats_keywords=["Python", "Django", "Redis", "Kubernetes"]
        )

        analysis = ATSGapAnalyzer.analyze(self.user, job)
        self.assertGreater(analysis["match_score"], 50)
        self.assertIn("Python", analysis["present_skills"])
        self.assertIn("Django", analysis["present_skills"])

    def test_knowledge_base_rest_api(self):
        response = self.client.get('/api/v3/knowledge-base/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["headline"], "Full-stack AI Systems Engineer")
        self.assertEqual(len(response.data["experiences"]), 1)
        self.assertEqual(response.data["experiences"][0]["company"], "Ridecell")

    @patch('llm.router.GeminiAdapter.generate')
    def test_v3_resume_tailor_api(self, mock_gemini):
        mock_gemini.return_value = {
            "type": "text",
            "text": '''{
                "title": "Tailored Resume",
                "header": {
                    "full_name": "Shivam Singh",
                    "headline": "Senior AI Infrastructure Engineer",
                    "email": "shivam@example.com"
                },
                "summary": "Tailored summary for Google...",
                "skills_groups": [{"category": "Languages", "skills": ["Python", "Django"]}],
                "experiences": [{
                    "company": "Ridecell",
                    "role": "Senior Software Engineer",
                    "start_date": "Jan 2022",
                    "end_date": "Present",
                    "bullet_points": ["Architected telematics pipeline handling 10k req/sec"]
                }],
                "projects": []
            }''',
            "provider": "gemini-flash"
        }

        job = JobPosting.objects.create(
            user_profile=self.user,
            company_name="Google",
            job_title="Senior Engineer",
            raw_text="Python Django Redis required"
        )

        response = self.client.post('/api/v3/resumes/tailor/', {
            "job_id": str(job.id)
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIn("latex_code", response.data)
        self.assertIn("ats_report", response.data)
        self.assertEqual(ResumeVersion.objects.count(), 1)

    def test_document_loader_and_detector(self):
        from knowledge_base.documents.detector import SectionDetector
        text = "SUMMARY\nSoftware Engineer with experience in Django.\nEXPERIENCE\nRidecell - Software Engineer\nSKILLS\nPython, Django, Redis"
        sections = SectionDetector.detect_sections(text)
        self.assertIn("Django", sections["summary"])
        self.assertIn("Ridecell", sections["experience"])
        self.assertIn("Python", sections["skills"])

    def test_latex_jinja_rendering(self):
        from resume.templates import get_or_create_default_template, ResumeTemplateRenderer
        template = get_or_create_default_template()
        spec = {
            "header": {
                "full_name": "Shivam Singh",
                "headline": "Senior Engineer",
                "email": "shivam@example.com",
                "phone": "+1-555",
                "location": "SF"
            },
            "summary": "Building scalable backend services.",
            "skills_groups": [{"category": "Languages", "skills": ["Python", "Django"]}],
            "experiences": [{
                "company": "Ridecell", "role": "Senior Engineer", "start_date": "2022", "end_date": "Present",
                "bullet_points": ["Architected telematics pipeline"]
            }],
            "projects": []
        }
        latex_code = ResumeTemplateRenderer.render(template, spec)
        self.assertIn(r"\begin{document}", latex_code)
        self.assertIn("Shivam Singh", latex_code)
        self.assertIn("Ridecell", latex_code)

    def test_resume_spec_diff_engine(self):
        from resume.diff import SpecDiffEngine
        spec_a = {"summary": "Experienced engineer.", "experiences": [], "projects": []}
        spec_b = {"summary": "Highly experienced systems engineer.", "experiences": [], "projects": []}
        report = SpecDiffEngine.diff_specs(spec_a, spec_b)
        self.assertEqual(len(report["summary_diff"]), 2)

    def test_multi_format_downloads(self):
        # Create dummy version to test HTML download view
        version = ResumeVersion.objects.create(
            user_profile=self.user,
            version_name="Google Tailored Resume",
            structured_spec={
                "header": {"full_name": "Shivam Singh", "headline": "Engineer", "email": "shivam@example.com"},
                "summary": "Building features.",
                "skills_groups": [],
                "experiences": [],
                "projects": []
            },
            latex_code=""
        )
        from django.urls import reverse
        url = reverse('resume-download', kwargs={'version_id': version.id}) + "?output=html"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/html")
        self.assertIn("Shivam Singh", response.content.decode())

    def test_knowledge_base_reset(self):
        # Initial check that experience exists
        self.assertEqual(Experience.objects.filter(user_profile=self.user).count(), 1)
        response = self.client.post('/api/v3/knowledge-base/reset/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Experience.objects.filter(user_profile=self.user).exists())

    def test_knowledge_base_enrich(self):
        # Create a new skill enrichment
        response = self.client.post('/api/v3/knowledge-base/enrich/', {
            "skill": {"category": "Languages", "name": "Go", "proficiency": "Expert"}
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Skill.objects.filter(user_profile=self.user, name="Go").exists())

    def test_prospecting_discover_validation(self):
        response = self.client.post('/api/v3/prospecting/discover/', {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_prospecting_reset(self):
        from prospecting.models import LeadCompany, DiscoveryRun
        # Create dummy record
        run = DiscoveryRun.objects.create(user_profile=self.user, keyword="courier", location="london")
        LeadCompany.objects.create(discovery_run=run, name="Test Express")
        self.assertEqual(LeadCompany.objects.count(), 1)
        
        response = self.client.post('/api/v3/prospecting/reset/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadCompany.objects.count(), 0)

    def test_prospecting_leads_list(self):
        from prospecting.models import LeadCompany, DiscoveryRun, WebsiteAnalysis
        run = DiscoveryRun.objects.create(user_profile=self.user, keyword="courier", location="london")
        c1 = LeadCompany.objects.create(discovery_run=run, name="Courier London", category="Courier Service", address="London, UK")
        WebsiteAnalysis.objects.create(company=c1, lead_score=8.5, description="A", lead_score_reason="B")
        
        c2 = LeadCompany.objects.create(discovery_run=run, name="Cargo Manchester", category="Freight", address="Manchester, UK")
        WebsiteAnalysis.objects.create(company=c2, lead_score=4.0, description="A", lead_score_reason="B")

        # 1. Base list response
        response = self.client.get('/api/v3/prospecting/leads/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("leads", data)
        self.assertEqual(data["total_count"], 2)
        self.assertTrue(any(l["name"] == "Courier London" for l in data["leads"]))
        self.assertIn("Courier Service", data["categories"])
        self.assertIn("Freight", data["categories"])

        # 2. Filter by category
        res_filter_cat = self.client.get('/api/v3/prospecting/leads/?category=Freight')
        self.assertEqual(res_filter_cat.status_code, 200)
        self.assertEqual(res_filter_cat.json()["total_count"], 1)
        self.assertEqual(res_filter_cat.json()["leads"][0]["name"], "Cargo Manchester")

        # 3. Filter by min score
        res_filter_score = self.client.get('/api/v3/prospecting/leads/?score_min=5.0')
        self.assertEqual(res_filter_score.status_code, 200)
        self.assertEqual(res_filter_score.json()["total_count"], 1)
        self.assertEqual(res_filter_score.json()["leads"][0]["name"], "Courier London")

        # 4. Pagination
        res_page = self.client.get('/api/v3/prospecting/leads/?page=1&page_size=1')
        self.assertEqual(res_page.status_code, 200)
        self.assertEqual(len(res_page.json()["leads"]), 1)
        self.assertEqual(res_page.json()["total_pages"], 2)

    def test_resume_template_upload(self):
        from resume.models import ResumeTemplate
        # 1. Test get template list
        response = self.client.get('/api/v3/resumes/templates/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 0)

        # 2. Test create LaTeX template
        response = self.client.post('/api/v3/resumes/templates/', {
            "name": "Classic",
            "latex_source": "hello {{ name }}"
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ResumeTemplate.objects.filter(name="Classic").count(), 1)

        # 3. Test list after creation
        response = self.client.get('/api/v3/resumes/templates/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)



