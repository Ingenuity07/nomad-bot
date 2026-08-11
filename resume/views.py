import os
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import FileResponse, HttpResponse

from knowledge_base.models import UserProfile, JobPosting
from resume.models import ResumeTemplate, ResumeVersion, ATSReport
from prospecting.views import get_default_user

logger = logging.getLogger(__name__)

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
            from knowledge_base.jobs.parser import JobParser
            parser = JobParser()
            job_posting = parser.parse_and_save(user, raw_job_text)
            
        template_name = request.data.get("template_name", "modern")
        from resume.pipelines.optimization_pipeline import ResumeOptimizationPipeline
        pipeline = ResumeOptimizationPipeline()
        try:
            res = pipeline.run(user, job_posting, template_name=template_name)
            return Response(res, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception("Resume tailoring failed")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ResumeTemplateListAPIView(APIView):
    """List available templates or create a new template by uploading LaTeX/PDF or entering source."""

    def get(self, request):
        templates = ResumeTemplate.objects.filter(is_active=True).order_by('-created_at')
        data = [{
            "id": str(t.id),
            "name": t.name,
            "latex_source": t.latex_source,
            "created_at": t.created_at
        } for t in templates]
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        name = request.data.get("name")
        latex_source = request.data.get("latex_source", "")
        uploaded_file = request.FILES.get("file")

        if not name:
            return Response({"error": "Template name is required"}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded_file:
            import tempfile
            from knowledge_base.documents.loader import DocumentLoader
            
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
                            
                        from llm.router import IntelligentRouter
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

        from resume.diff import SpecDiffEngine
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

        if fmt == "html":
            from resume.renderers.html import HTMLResumeRenderer
            html_content = HTMLResumeRenderer.render(version.structured_spec)
            response = HttpResponse(html_content, content_type="text/html")
            response["Content-Disposition"] = f'attachment; filename="resume_{version_id[:8]}.html"'
            return response
            
        elif fmt == "docx":
            from resume.renderers.docx import DOCXResumeRenderer
            docx_path = DOCXResumeRenderer.render(version.structured_spec)
            response = FileResponse(open(docx_path, "rb"), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            response["Content-Disposition"] = f'attachment; filename="resume_{version_id[:8]}.docx"'
            return response
            
        elif fmt == "markdown" or fmt == "md":
            from resume.renderers.markdown import MarkdownResumeRenderer
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
