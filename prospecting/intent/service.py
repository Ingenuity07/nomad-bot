import logging
from django.db import transaction
from django.utils import timezone
from prospecting.models import (
    ProspectingRequest, ProspectingSpecificationVersion, Discovery, DiscoveryRun, UserProfile
)
from prospecting.intent.schemas import ProspectingSpecification, IntentParseResult, Provenance
from prospecting.intent.parser import ProspectingIntentParser
from prospecting.intent.validator import ProspectingSpecificationValidator, SpecificationValidationError
from prospecting.tasks import discover_campaign_async
import os

logger = logging.getLogger(__name__)

class ProspectingIntentService:
    """Orchestrates the entire natural-language intake, parsing, and confirmation lifecycle."""

    @staticmethod
    def create_intake_request(user_profile: UserProfile, objective: str, target: str = "", qualification: str = "") -> ProspectingRequest:
        logger.info(f"Creating ProspectingRequest for user: {user_profile.username} (Objective: {objective[:50]}...)")
        request = ProspectingRequest.objects.create(
            user_profile=user_profile,
            raw_objective=objective,
            raw_target=target,
            raw_qualification=qualification,
            status='DRAFT'
        )
        logger.info(f"Successfully created ProspectingRequest {request.id}")
        return request

    @staticmethod
    def parse_request(request_id: str) -> ProspectingSpecificationVersion:
        logger.info(f"Initializing intent parsing run for Request ID: {request_id}")
        request = ProspectingRequest.objects.get(id=request_id)
        request.status = 'PARSING'
        request.save()

        parser = ProspectingIntentParser()
        try:
            from llm.context import LLMRequestContext
            with LLMRequestContext(
                correlation_id=f"prospecting_request:{request_id}",
                operation="prospecting.intent_parser",
                metadata={
                    "prospecting_request_id": str(request_id),
                }
            ):
                parsed_result = parser.parse_intent(
                    objective=request.raw_objective or "",
                    target=request.raw_target or "",
                    qualification=request.raw_qualification or "",
                    clarification_history=request.clarification_history
                )
        except Exception as e:
            logger.exception(f"Intent parser failed for request {request_id}")
            request.status = 'FAILED'
            request.save()
            raise e

        # Resolve next version number
        last_version = request.spec_versions.order_by('-version').first()
        next_version_num = (last_version.version + 1) if last_version else 1

        # Create immutable version record
        status_map = {
            "READY_FOR_REVIEW": "READY_FOR_REVIEW",
            "NEEDS_CLARIFICATION": "DRAFT",
            "INVALID": "DRAFT"
        }
        ver_status = status_map.get(parsed_result.status, "DRAFT")

        spec_version = ProspectingSpecificationVersion.objects.create(
            request=request,
            version=next_version_num,
            schema_version="v1",
            specification_json=parsed_result.specification.model_dump(),
            status=ver_status,
            prompt_version="v1",
            parser_model=parsed_result.parser_model or "gemini-2.5-flash",
            parser_provider=parsed_result.parser_provider or "gemini-flash"
        )

        if parsed_result.status == 'NEEDS_CLARIFICATION' and parsed_result.clarification_questions:
            question = parsed_result.clarification_questions[0]
            history = list(request.clarification_history)
            if history and history[-1].get("answer") == "":
                history[-1]["question"] = question
            else:
                history.append({"question": question, "answer": ""})
            request.clarification_history = history

        request.status = parsed_result.status
        request.save()
        logger.info(
            f"Successfully parsed Request {request_id} into Specification Version {next_version_num} "
            f"with Status: {ver_status} using Model: {spec_version.parser_model}"
        )
        return spec_version

    @staticmethod
    def submit_clarification(request_id: str, question: str, answer: str) -> ProspectingSpecificationVersion:
        logger.info(f"Submitting clarification answer for Request ID: {request_id} (Q: {question} | A: {answer})")
        request = ProspectingRequest.objects.get(id=request_id)
        history = list(request.clarification_history)
        if history and history[-1].get("answer") == "":
            history[-1]["answer"] = answer
        else:
            history.append({"question": question, "answer": answer})
        request.clarification_history = history
        request.save()

        # Reparse updated context
        return ProspectingIntentService.parse_request(request_id)

    @staticmethod
    def update_specification(request_id: str, spec_data: dict) -> ProspectingSpecificationVersion:
        logger.info(f"User requested specification manual update for Request ID: {request_id}")
        request = ProspectingRequest.objects.get(id=request_id)
        if request.status == 'CONFIRMED':
            logger.error(f"Failed updating specification for Request {request_id}: already confirmed.")
            raise ValueError("Cannot edit a specification that has already been confirmed.")

        # 1. Validate against Pydantic schema
        spec_obj = ProspectingSpecification.model_validate(spec_data)

        # 2. Run programmatic validations
        validation_errors = ProspectingSpecificationValidator.validate_specification(spec_obj)
        if validation_errors:
            logger.error(f"Specification validation failed for Request {request_id}: {validation_errors}")
            raise SpecificationValidationError(validation_errors)

        # 3. Create a new immutable version
        last_version = request.spec_versions.order_by('-version').first()
        next_version_num = (last_version.version + 1) if last_version else 1

        spec_version = ProspectingSpecificationVersion.objects.create(
            request=request,
            version=next_version_num,
            schema_version="v1",
            specification_json=spec_obj.model_dump(),
            status="READY_FOR_REVIEW"
        )

        request.status = "READY_FOR_REVIEW"
        request.save()
        logger.info(f"Successfully updated Specification for Request {request_id} to Version {next_version_num} (Status: READY_FOR_REVIEW)")
        return spec_version

    @staticmethod
    def confirm_specification(request_id: str, version: int, confirmed_by: UserProfile) -> Discovery:
        logger.info(f"Initiating transaction-atomic confirmation for Request {request_id} (Spec Version: {version})")
        with transaction.atomic():
            # Lock request row for transaction safety
            request = ProspectingRequest.objects.select_for_update().get(id=request_id)

            # Check if this specification version is already confirmed (idempotency check)
            existing_discovery = Discovery.objects.filter(
                prospecting_request=request,
                specification_version__version=version
            ).first()

            if existing_discovery:
                from prospecting.campaigns import ensure_campaign_for_run
                for existing_run in existing_discovery.runs.all():
                    ensure_campaign_for_run(
                        existing_run,
                        user=confirmed_by,
                        prospecting_request=request,
                    )
                logger.info(f"Discovery already exists for request {request_id} version {version}. Returning existing record.")
                return existing_discovery

            if request.status == 'CONFIRMED':
                logger.error(f"Cannot confirm Spec Version {version} for Request {request_id}: already confirmed under another version.")
                raise ValueError("ProspectingRequest is already confirmed under another version.")

            # Load target version
            spec_version = request.spec_versions.select_for_update().filter(version=version).first()
            if not spec_version:
                logger.error(f"Specification version {version} not found for request {request_id}.")
                raise ValueError(f"Specification version {version} not found for request {request_id}.")

            if spec_version.status == 'CONFIRMED':
                raise ValueError("This specification version has already been confirmed.")

            # Programmatically validate to ensure clean payload before confirming
            spec_obj = ProspectingSpecification.model_validate(spec_version.specification_json)
            validation_errors = ProspectingSpecificationValidator.validate_specification(spec_obj)
            if validation_errors:
                logger.error(f"Failed confirming specification: validation failed with errors: {validation_errors}")
                raise SpecificationValidationError(validation_errors)

            # Mark LLM_INFERRED fields as USER_CONFIRMED
            spec_obj.confirm_all_inferred()
            spec_version.specification_json = spec_obj.model_dump()

            # Confirm specification version
            spec_version.status = 'CONFIRMED'
            spec_version.confirmed_at = timezone.now()
            spec_version.confirmed_by = confirmed_by
            spec_version.save()

            # Confirm request
            request.status = 'CONFIRMED'
            request.save()

            # Create Discovery record
            discovery = Discovery.objects.create(
                user_profile=confirmed_by,
                prospecting_request=request,
                specification_version=spec_version
            )

            # Prepare DiscoveryRun
            keyword = spec_obj.target.description.value or spec_obj.objective.value
            location = ", ".join(spec_obj.geography.countries.value + spec_obj.geography.cities.value) or "Global"
            
            run = DiscoveryRun.objects.create(
                user_profile=confirmed_by,
                keyword=keyword[:1000],
                location=location[:1000],
                status='pending',
                discovery=discovery,
                prospecting_request=request,
                specification_version=spec_version
            )

            from prospecting.campaigns import ensure_campaign_for_run
            ensure_campaign_for_run(
                run,
                user=confirmed_by,
                prospecting_request=request,
                product_description=spec_obj.problem_hypothesis.solution_or_offering.value,
                problem_statement=spec_obj.problem_hypothesis.problem.value,
                geography={
                    "countries": spec_obj.geography.countries.value,
                    "regions": spec_obj.geography.regions.value,
                    "cities": spec_obj.geography.cities.value,
                    "radius": spec_obj.geography.radius.value,
                    "scope": spec_obj.geography.scope.value,
                },
            )

            # Dispatch async discovery runner on commit
            logger.info(f"Enqueuing discovery run {run.id} asynchronously to Celery pool")
            transaction.on_commit(lambda: discover_campaign_async.delay(str(run.id)))

            logger.info(f"Request {request_id} Spec Version {version} successfully locked, confirmed, and run {run.id} dispatched.")
            return discovery
