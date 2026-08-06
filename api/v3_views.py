import os
import logging
logger = logging.getLogger(__name__)
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from memory.models import (
    UserProfile, ProfessionalKnowledgeBase, Experience, Project, Skill,
    JobPosting, ResumeVersion, ATSReport, ApplicationTracker
)
from core.resume.ingestion import ResumeIngestionEngine
from core.jobs.parser import JobParser
from core.jobs.ats_analyzer import ATSGapAnalyzer
from core.agents.v3_tailor_agent import V3TailorAgent

def get_default_user():
    user, _ = UserProfile.objects.get_or_create(
        username='default_user',
        defaults={'email': 'default@example.com', 'full_name': 'Shivam Singh'}
    )
    return user


class KnowledgeBaseAPIView(APIView):
    """View and update the user's Professional Knowledge Base."""
    
    def get(self, request):
        user = get_default_user()
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user)
        experiences = Experience.objects.filter(user_profile=user)
        projects = Project.objects.filter(user_profile=user)
        skills = Skill.objects.filter(user_profile=user)

        return Response({
            "id": str(kb.id),
            "title": kb.title,
            "headline": kb.headline,
            "summary": kb.summary,
            "years_of_experience": kb.years_of_experience,
            "experiences": [{
                "id": str(e.id), "company": e.company, "role": e.role,
                "start_date": e.start_date, "end_date": e.end_date,
                "bullet_points": e.bullet_points, "tech_stack": e.tech_stack
            } for e in experiences],
            "projects": [{
                "id": str(p.id), "title": p.title, "description": p.description,
                "tech_stack": p.tech_stack, "impact_metrics": p.impact_metrics
            } for p in projects],
            "skills": [{
                "id": str(s.id), "category": s.category, "name": s.name, "proficiency": s.proficiency
            } for s in skills]
        }, status=status.HTTP_200_OK)

    def post(self, request):
        user = get_default_user()
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user)
        kb.title = request.data.get("title", kb.title)
        kb.headline = request.data.get("headline", kb.headline)
        kb.summary = request.data.get("summary", kb.summary)
        kb.years_of_experience = request.data.get("years_of_experience", kb.years_of_experience)
        kb.save()
        return Response({"status": "updated"}, status=status.HTTP_200_OK)


class ResumeIngestAPIView(APIView):
    """Parse raw resume text or uploaded PDF/DOCX/MD documents into structured Knowledge Base entities."""

    def post(self, request):
        user = get_default_user()
        uploaded_file = request.FILES.get("file")
        raw_text = request.data.get("resume_text", "")

        if not uploaded_file and not raw_text:
            return Response({"error": "file or resume_text is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Handle uploaded file extraction
        if uploaded_file:
            import tempfile
            from core.documents.loader import DocumentLoader
            from core.documents.ocr import OCRService

            ext = os.path.splitext(uploaded_file.name)[1].lower()
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
                for chunk in uploaded_file.chunks():
                    temp_file.write(chunk)
                temp_file_path = temp_file.name

            try:
                # Load text
                extracted = DocumentLoader.load(temp_file_path)
                text = extracted.get("text", "")

                # OCR check if needed
                ocr_service = OCRService()
                if not ocr_service.is_searchable(text):
                    text = ocr_service.extract_via_ocr(temp_file_path)
            finally:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
        else:
            text = raw_text

        if not text.strip():
            return Response({"error": "No parseable text extracted from the document"}, status=status.HTTP_400_BAD_REQUEST)

        engine = ResumeIngestionEngine()
        try:
            res = engine.parse_and_ingest(user, text)
            return Response(res, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class KnowledgeBaseResetAPIView(APIView):
    """Resets the user's Professional Knowledge Base, experiences, projects, skills, versions, and application records."""

    def post(self, request):
        user = get_default_user()
        
        # Clear everything linked to the user
        Experience.objects.filter(user_profile=user).delete()
        Project.objects.filter(user_profile=user).delete()
        Skill.objects.filter(user_profile=user).delete()
        ResumeVersion.objects.filter(user_profile=user).delete()
        ApplicationTracker.objects.filter(user_profile=user).delete()
        
        # Reset Knowledge Base details
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user)
        kb.title = ""
        kb.headline = ""
        kb.summary = ""
        kb.years_of_experience = 0
        kb.save()
        
        return Response({"status": "reset_completed"}, status=status.HTTP_200_OK)


class KnowledgeBaseEnrichAPIView(APIView):
    """Enriches the user's Professional Knowledge Base by adding experience, project, or skill items."""

    def post(self, request):
        user = get_default_user()
        
        experience_data = request.data.get("experience")
        project_data = request.data.get("project")
        skill_data = request.data.get("skill")
        
        if experience_data:
            Experience.objects.create(
                user_profile=user,
                company=experience_data.get("company"),
                role=experience_data.get("role"),
                location=experience_data.get("location", ""),
                start_date=experience_data.get("start_date", ""),
                end_date=experience_data.get("end_date", ""),
                bullet_points=experience_data.get("bullet_points", []),
                tech_stack=experience_data.get("tech_stack", [])
            )
            
        if project_data:
            Project.objects.create(
                user_profile=user,
                title=project_data.get("title"),
                description=project_data.get("description", ""),
                tech_stack=project_data.get("tech_stack", []),
                impact_metrics=project_data.get("impact_metrics", [])
            )
            
        if skill_data:
            Skill.objects.create(
                user_profile=user,
                category=skill_data.get("category", "General"),
                name=skill_data.get("name"),
                proficiency=skill_data.get("proficiency", "Intermediate")
            )
            
        return Response({"status": "enrichment_completed"}, status=status.HTTP_200_OK)



class JobParseAPIView(APIView):
    """Parse a raw job description and compute initial ATS match score."""

    def post(self, request):
        user = get_default_user()
        raw_job_text = request.data.get("job_text", "")
        job_url = request.data.get("job_url", "")
        
        if not raw_job_text:
            return Response({"error": "job_text is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        parser = JobParser()
        try:
            job_posting = parser.parse_and_save(user, raw_job_text, job_url=job_url)
            ats_analysis = ATSGapAnalyzer.analyze(user, job_posting)
            
            return Response({
                "job_id": str(job_posting.id),
                "company_name": job_posting.company_name,
                "job_title": job_posting.job_title,
                "required_skills": job_posting.required_skills,
                "ats_keywords": job_posting.ats_keywords,
                "ats_analysis": ats_analysis
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ResumeTailorAPIView(APIView):
    """Execute the V3 Tailoring Agent to create a tailored resume version & deterministic LaTeX/PDF."""

    def post(self, request):
        user = get_default_user()
        job_id = request.data.get("job_id")
        raw_job_text = request.data.get("job_text")
        
        if not job_id and not raw_job_text:
            return Response({"error": "job_id or job_text is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        if job_id:
            try:
                job_posting = JobPosting.objects.get(id=job_id, user_profile=user)
            except JobPosting.DoesNotExist:
                return Response({"error": "Job posting not found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            parser = JobParser()
            job_posting = parser.parse_and_save(user, raw_job_text)
            
        template_name = request.data.get("template_name", "modern")
        from core.pipelines.optimization_pipeline import ResumeOptimizationPipeline
        pipeline = ResumeOptimizationPipeline()
        try:
            res = pipeline.run(user, job_posting, template_name=template_name)
            return Response(res, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ResumeVersionListAPIView(APIView):
    """List all generated immutable resume versions."""

    def get(self, request):
        user = get_default_user()
        versions = ResumeVersion.objects.filter(user_profile=user)
        return Response([{
            "id": str(v.id),
            "version_name": v.version_name,
            "company_name": v.job_posting.company_name if v.job_posting else "General",
            "job_title": v.job_posting.job_title if v.job_posting else "Software Engineer",
            "ats_score": v.ats_score,
            "generation_latency_ms": v.generation_latency_ms,
            "llm_provider": v.llm_provider,
            "created_at": v.created_at.isoformat()
        } for v in versions], status=status.HTTP_200_OK)


class ResumeVersionDetailAPIView(APIView):
    """Retrieve full details, LaTeX code, and ATS report for a specific resume version."""

    def get(self, request, version_id):
        user = get_default_user()
        try:
            version = ResumeVersion.objects.get(id=version_id, user_profile=user)
        except ResumeVersion.DoesNotExist:
            return Response({"error": "Resume version not found"}, status=status.HTTP_404_NOT_FOUND)

        ats_report = getattr(version, "ats_report", None)
        report_data = None
        if ats_report:
            report_data = {
                "match_score": ats_report.match_score,
                "present_skills": ats_report.present_skills,
                "missing_skills": ats_report.missing_skills,
                "weak_skills": ats_report.weak_skills,
                "improvement_suggestions": ats_report.improvement_suggestions
            }

        return Response({
            "id": str(version.id),
            "version_name": version.version_name,
            "structured_spec": version.structured_spec,
            "latex_code": version.latex_code,
            "pdf_path": version.pdf_path,
            "ats_score": version.ats_score,
            "ats_report": report_data,
            "created_at": version.created_at.isoformat()
        }, status=status.HTTP_200_OK)


class ApplicationTrackerAPIView(APIView):
    """View and update job applications status."""

    def get(self, request):
        user = get_default_user()
        apps = ApplicationTracker.objects.filter(user_profile=user)
        return Response([{
            "id": str(a.id),
            "company_name": a.job_posting.company_name,
            "job_title": a.job_posting.job_title,
            "status": a.status,
            "resume_version": a.resume_version.version_name if a.resume_version else "Default",
            "ats_score": a.resume_version.ats_score if a.resume_version else 0,
            "created_at": a.created_at.isoformat()
        } for a in apps], status=status.HTTP_200_OK)


class ResumeDiffAPIView(APIView):
    """Compares two resume specifications and returns Git-style diffs."""

    def get(self, request):
        user = get_default_user()
        old_id = request.query_params.get("old_id")
        new_id = request.query_params.get("new_id")

        if not old_id or not new_id:
            return Response({"error": "old_id and new_id are required query parameters"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            old_version = ResumeVersion.objects.get(id=old_id, user_profile=user)
            new_version = ResumeVersion.objects.get(id=new_id, user_profile=user)
        except ResumeVersion.DoesNotExist:
            return Response({"error": "One or both resume versions not found"}, status=status.HTTP_404_NOT_FOUND)

        from core.resume.diff import SpecDiffEngine
        diff_report = SpecDiffEngine.diff_specs(old_version.structured_spec, new_version.structured_spec)
        return Response(diff_report, status=status.HTTP_200_OK)


class ResumeDownloadAPIView(APIView):
    """Compiles and downloads a resume version in HTML, DOCX, Markdown, or PDF format."""

    def get(self, request, version_id):
        user = get_default_user()
        fmt = request.query_params.get("output", "pdf").lower()

        try:
            version = ResumeVersion.objects.get(id=version_id, user_profile=user)
        except ResumeVersion.DoesNotExist:
            return Response({"error": "Resume version not found"}, status=status.HTTP_404_NOT_FOUND)

        from django.http import HttpResponse, FileResponse

        if fmt == "html":
            from core.resume.renderers.html import HTMLResumeRenderer
            html_content = HTMLResumeRenderer.render(version.structured_spec)
            response = HttpResponse(html_content, content_type="text/html")
            response["Content-Disposition"] = f'attachment; filename="resume_{version_id[:8]}.html"'
            return response
            
        elif fmt == "docx":
            from core.resume.renderers.docx import DOCXResumeRenderer
            docx_path = DOCXResumeRenderer.render(version.structured_spec)
            response = FileResponse(open(docx_path, "rb"), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            response["Content-Disposition"] = f'attachment; filename="resume_{version_id[:8]}.docx"'
            return response
            
        elif fmt == "markdown" or fmt == "md":
            from core.resume.renderers.markdown import MarkdownResumeRenderer
            md_content = MarkdownResumeRenderer.render(version.structured_spec)
            response = HttpResponse(md_content, content_type="text/markdown")
            response["Content-Disposition"] = f'attachment; filename="resume_{version_id[:8]}.md"'
            return response
            
        else:  # Fallback to standard PDF file
            if version.pdf_path and os.path.exists(version.pdf_path):
                response = FileResponse(open(version.pdf_path, "rb"), content_type="application/pdf")
                response["Content-Disposition"] = f'attachment; filename="resume_{version_id[:8]}.pdf"'
                return response
            return Response({"error": "PDF file not found"}, status=status.HTTP_404_NOT_FOUND)


class ProspectingDiscoverAPIView(APIView):
    """Trigger a new Lead Generation discovery and qualification run."""

    def post(self, request):
        user = get_default_user()
        keyword = request.data.get("keyword", "").strip()
        location = request.data.get("location", "").strip()

        if not keyword or not location:
            return Response({"error": "keyword and location are required"}, status=status.HTTP_400_BAD_REQUEST)

        from memory.models import DiscoveryRun, LeadCompany
        from core.prospecting.discovery import BusinessDiscoveryEngine
        from core.prospecting.contact import ContactExtractor
        from core.prospecting.analyzer import WebsiteAnalyzer

        # Create discovery run record
        run = DiscoveryRun.objects.create(
            user_profile=user,
            keyword=keyword,
            location=location,
            status='running'
        )

        try:
            # 1. Discover Businesses
            discovery_engine = BusinessDiscoveryEngine()
            companies = discovery_engine.discover_businesses(run)

            # Limit to top 5 results for synchronous execution safety (avoiding timeout)
            leads_to_process = LeadCompany.objects.filter(discovery_run=run)[:5]

            # 2. Extract Contacts & Analyze Websites
            analyzer = WebsiteAnalyzer()
            for company in leads_to_process:
                ContactExtractor.extract_contacts(company)
                analyzer.analyze_website(company)

            run.status = 'completed'
            run.total_leads_found = len(companies)
            run.save()

            return Response({
                "status": "success",
                "run_id": str(run.id),
                "leads_found": run.total_leads_found,
                "processed_leads_count": leads_to_process.count()
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            run.status = 'failed'
            run.save()
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingLeadsAPIView(APIView):
    """Retrieve all leads in the CRM along with contacts and qualification scores, supporting filtering and pagination."""

    def get(self, request):
        from memory.models import LeadCompany
        
        # 1. Fetch query filters
        score_min = request.query_params.get("score_min")
        location = request.query_params.get("location")
        category = request.query_params.get("category")
        
        # 2. Fetch pagination params
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 10))
        except ValueError:
            page = 1
            page_size = 10

        # Build filter set
        queryset = LeadCompany.objects.all()
        
        if score_min:
            try:
                queryset = queryset.filter(analysis__lead_score__gte=float(score_min))
            except ValueError:
                pass
        if location and location.strip():
            queryset = queryset.filter(address__icontains=location.strip())
        if category and category.strip():
            queryset = queryset.filter(category__iexact=category.strip())
            
        queryset = queryset.order_by('-analysis__lead_score', 'name')
        
        total_count = queryset.count()
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        
        # Slice queryset for pagination
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_companies = queryset[start_idx:end_idx]

        leads = []
        for c in paginated_companies:
            contacts = [{
                "id": str(con.id),
                "email": con.email,
                "phone": con.phone,
                "linkedin": con.linkedin,
                "role": con.role
            } for con in c.contacts.all()]

            analysis_data = {}
            if hasattr(c, 'analysis'):
                analysis_data = {
                    "description": c.analysis.description,
                    "has_delivery": c.analysis.has_delivery,
                    "has_scheduling": c.analysis.has_scheduling,
                    "needs_routing": c.analysis.needs_routing,
                    "fleet_size_estimate": c.analysis.fleet_size_estimate,
                    "lead_score": c.analysis.lead_score,
                    "lead_score_reason": c.analysis.lead_score_reason
                }

            leads.append({
                "id": str(c.id),
                "name": c.name,
                "website": c.website,
                "phone": c.phone,
                "address": c.address,
                "category": c.category,
                "contacts": contacts,
                "analysis": analysis_data
            })

        # Fetch unique categories for dynamic UI dropdown
        unique_categories = list(LeadCompany.objects.values_list('category', flat=True).distinct())
        unique_categories = sorted(list(set([cat for cat in unique_categories if cat])))

        return Response({
            "leads": leads,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "categories": unique_categories
        }, status=status.HTTP_200_OK)


class ProspectingResetAPIView(APIView):
    """Clears all discovery runs and lead listings from the CRM."""

    def post(self, request):
        from memory.models import DiscoveryRun, LeadCompany, LeadContact, WebsiteAnalysis
        LeadContact.objects.all().delete()
        WebsiteAnalysis.objects.all().delete()
        LeadCompany.objects.all().delete()
        DiscoveryRun.objects.all().delete()
        return Response({"status": "reset_completed"}, status=status.HTTP_200_OK)


class ResumeTemplateListAPIView(APIView):
    """List available templates or create a new template by uploading LaTeX/PDF or entering source."""

    def get(self, request):
        from memory.models import ResumeTemplate
        templates = ResumeTemplate.objects.filter(is_active=True).order_by('-created_at')
        data = [{
            "id": str(t.id),
            "name": t.name,
            "latex_source": t.latex_source,
            "created_at": t.created_at
        } for t in templates]
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        from memory.models import ResumeTemplate
        name = request.data.get("name")
        latex_source = request.data.get("latex_source", "")
        uploaded_file = request.FILES.get("file")

        if not name:
            return Response({"error": "Template name is required"}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded_file:
            import tempfile
            from core.documents.loader import DocumentLoader
            
            ext = os.path.splitext(uploaded_file.name)[1].lower()
            if ext == ".tex":
                try:
                    latex_source = ""
                    for chunk in uploaded_file.chunks():
                        latex_source += chunk.decode("utf-8", errors="ignore")
                except Exception as e:
                    logger.exception("Failed to read LaTeX template file")
                    return Response({"error": f"Failed to read LaTeX file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
            elif ext == ".pdf":
                # Check magic bytes: if local environment fallback occurred, the PDF is actually LaTeX text
                is_real_pdf = False
                try:
                    uploaded_file.seek(0)
                    header = uploaded_file.read(4)
                    if header == b"%PDF":
                        is_real_pdf = True
                    uploaded_file.seek(0)
                except Exception:
                    pass

                if not is_real_pdf:
                    try:
                        latex_source = ""
                        for chunk in uploaded_file.chunks():
                            latex_source += chunk.decode("utf-8", errors="ignore")
                    except Exception as e:
                        logger.exception("Failed to read fallback LaTeX template file")
                        return Response({"error": f"Failed to read template file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
                else:
                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
                        for chunk in uploaded_file.chunks():
                            temp_file.write(chunk)
                        temp_file_path = temp_file.name

                    try:
                        extracted = DocumentLoader.load(temp_file_path)
                        pdf_text = extracted.get("text", "")
                        
                        if not pdf_text.strip():
                            return Response({"error": "Failed to extract text from PDF template"}, status=status.HTTP_400_BAD_REQUEST)
                            
                        from core.llm.router import IntelligentRouter
                        router = IntelligentRouter()
                        system_prompt = (
                            "You are an expert LaTeX and Jinja2 resume template designer.\n"
                            "Convert the raw text of the parsed resume PDF into a fully compilable LaTeX resume template "
                            "using Jinja2 variables and loops. The template must be generic, clean, compile cleanly, and match the original layout structure.\n\n"
                            "Use standard Jinja2 placeholder tags:\n"
                            "- {{ name }}, {{ email }}, {{ phone }}, {{ linkedin }}, {{ github }}, {{ website }}, {{ headline }}, {{ summary }}\n"
                            "- {% for exp in work_experience %}\n"
                            "  \\subsection*{{ exp.company }}\n"
                            "  \\textbf{Role: {{ exp.role }}} | Dates: {{ exp.start_date }} - {{ exp.end_date }} | Location: {{ exp.location }}\n"
                            "  \\begin{itemize}\n"
                            "    {% for bullet in exp.bullet_points %}\n"
                            "      \\item {{ bullet }}\n"
                            "    {% endfor %}\n"
                            "  \\end{itemize}\n"
                            "- {% endfor %}\n"
                            "- {% for proj in projects %}\n"
                            "  \\subsection*{{ proj.name }}\n"
                            "  \\textbf{Role: {{ proj.role }}} | Description: {{ proj.description }}\n"
                            "  \\begin{itemize}\n"
                            "    {% for bullet in proj.bullet_points %}\n"
                            "      \\item {{ bullet }}\n"
                            "    {% endfor %}\n"
                            "  \\end{itemize}\n"
                            "- {% endfor %}\n"
                            "- {% for skill in skills %}\n"
                            "  \\item \\textbf{ {{ skill.category }}:} {{ skill.name }} (Proficiency: {{ skill.proficiency }})\n"
                            "- {% endfor %}\n\n"
                            "Return ONLY the raw compilable LaTeX code with the Jinja2 loops/placeholders inside. Do not wrap in markdown blocks."
                        )
                        prompt = f"Convert this parsed resume text into a Jinja2 LaTeX resume template:\n\n{pdf_text}"
                        result = router.generate(
                            prompt=prompt,
                            system_prompt=system_prompt
                        )
                        latex_source = result.get("text", "")
                        if "```latex" in latex_source:
                            latex_source = latex_source.split("```latex")[1].split("```")[0].strip()
                        elif "```" in latex_source:
                            latex_source = latex_source.split("```")[1].split("```")[0].strip()
                    except Exception as e:
                        logger.exception("Failed to translate PDF template")
                        return Response({"error": f"Failed to translate PDF template: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    finally:
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
            else:
                return Response({"error": "Unsupported file format. Please upload .tex or .pdf templates"}, status=status.HTTP_400_BAD_REQUEST)

        if not latex_source or not latex_source.strip():
            return Response({"error": "Template content (latex_source) is required"}, status=status.HTTP_400_BAD_REQUEST)

        template = ResumeTemplate.objects.create(
            name=name,
            latex_source=latex_source,
            is_active=True
        )
        return Response({
            "id": str(template.id),
            "name": template.name,
            "latex_source": template.latex_source
        }, status=status.HTTP_201_CREATED)

