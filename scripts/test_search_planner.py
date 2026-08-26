#!/usr/bin/env python
import os
import sys
import django
import json
import argparse

# Setup Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
except NameError:
    # Fallback for interactive shells (e.g. IPython, Jupyter)
    sys.path.append(os.getcwd())
django.setup()

# Close stale DB connections (handles long-lived IPython/Django shells)
from django.db import connections
connections.close_all()

from llm.router import IntelligentRouter
from llm.enums import LLMComplexity, LLMOperation
from llm.contracts import LLMRequest

def run_test(iterations: int):
    # Constant input from the last run
    objective = "Prospect courier and delivery companies to sell route planning and fleet optimization software"
    target_description = "Courier, delivery, and logistics companies operating fleets with multiple drivers"

    print("=" * 70)
    print("🚀 Starting Discovery & Outreach Prompt Pipeline Test")
    print(f"Objective: {objective}")
    print(f"Target Description: {target_description}")
    print("=" * 70)

    router = IntelligentRouter()

    for i in range(1, iterations + 1):
        print(f"\n--- Iteration {i}/{iterations} ---")
        
        # --- STAGE 1: Search Planner (Complexity: STANDARD) ---
        print("\n[Stage 1] Executing Search Planner...")
        req_planner = LLMRequest(
            operation=LLMOperation.GENERATE,
            complexity=LLMComplexity.STANDARD,
            prompt_key="prospecting.search_planner.user",
            system_prompt_key="prospecting.search_planner.system",
            variables={
                "target_description": target_description,
                "objective": objective
            },
            metadata={"domain": "prospecting", "test_run": "true", "stage": "planner"}
        )
        res_planner = router.execute(req_planner)
        
        search_queries = []
        if res_planner.is_success():
            print(f"✅ Planner Success! (Model: {res_planner.model} | Provider: {res_planner.provider} | Attempts: {res_planner.attempts})")
            print(f"Latency: {res_planner.latency_ms}ms | Tokens: {res_planner.usage.get('total_tokens', 0)}")
            try:
                parsed = json.loads(res_planner.raw_text)
                search_queries = parsed.get("search_queries", [])
                print(f"Planned Categories: {search_queries}")
            except json.JSONDecodeError:
                print(f"Failed to parse text: {res_planner.raw_text}")
        else:
            print(f"❌ Planner Failed! Error: {res_planner.error_message}")
            continue

        # --- STAGE 2: Keyword Extractor (Complexity: SIMPLE) ---
        print("\n[Stage 2] Executing Keyword Extractor for each category...")
        for query in search_queries:
            print(f"\n👉 Optimizing category: '{query}'")
            req_extractor = LLMRequest(
                operation=LLMOperation.GENERATE,
                complexity=LLMComplexity.SIMPLE,
                prompt_key="prospecting.keyword_extractor.user",
                system_prompt_key="prospecting.keyword_extractor.system",
                variables={
                    "search_keyword": query
                },
                metadata={"domain": "prospecting", "test_run": "true", "stage": "extractor"}
            )
            res_extractor = router.execute(req_extractor)
            if res_extractor.is_success():
                print(f"  ✅ Extractor Success! (Model: {res_extractor.model} | Provider: {res_extractor.provider} | Attempts: {res_extractor.attempts})")
                print(f"  Latency: {res_extractor.latency_ms}ms | Tokens: {res_extractor.usage.get('total_tokens', 0)}")
                try:
                    parsed_ext = json.loads(res_extractor.raw_text)
                    keywords = parsed_ext.get("keywords", [])
                    print(f"  Extracted Keywords: {keywords}")
                except json.JSONDecodeError:
                    print(f"  Raw Text: {res_extractor.raw_text}")
            else:
                print(f"  ❌ Extractor Failed! Error: {res_extractor.error_message}")

        # --- STAGE 3: Website Suitability Qualifier (Complexity: COMPLEX) ---
        print("\n[Stage 3] Executing Website Suitability Qualifier...")
        
        simulated_company = "Leeds Rapid Deliveries Ltd."
        simulated_category = "Courier Services"
        simulated_content = (
            "Welcome to Leeds Rapid Deliveries Ltd. We are the premier parcel and freight courier service "
            "operating across West Yorkshire. Our fleet of 18 delivery drivers operates daily dispatch and "
            "last-mile courier services for commercial and residential packages. We specialize in hot shot delivery "
            "and scheduled courier distribution."
        )
        
        print(f"👉 Analyzing simulated site for: '{simulated_company}' ({simulated_category})")
        print(f"Scraped Content: \"{simulated_content}\"")
        
        req_qualifier = LLMRequest(
            operation=LLMOperation.GENERATE,
            complexity=LLMComplexity.COMPLEX,
            prompt_key="prospecting.web_qualifier.user",
            system_prompt_key="prospecting.web_qualifier.system",
            variables={
                "company_name": simulated_company,
                "category": simulated_category,
                "scraped_content": simulated_content
            },
            metadata={"domain": "prospecting", "test_run": "true", "stage": "qualifier"}
        )
        res_qualifier = router.execute(req_qualifier)
        
        if res_qualifier.is_success():
            print(f"\n  ✅ Qualifier Success! (Model: {res_qualifier.model} | Provider: {res_qualifier.provider} | Attempts: {res_qualifier.attempts})")
            print(f"  Latency: {res_qualifier.latency_ms}ms | Tokens: {res_qualifier.usage.get('total_tokens', 0)}")
            try:
                parsed_qual = json.loads(res_qualifier.raw_text)
                print("Qualification JSON Output:")
                print(json.dumps(parsed_qual, indent=2))
            except json.JSONDecodeError:
                print(f"  Raw Text: {res_qualifier.raw_text}")
        else:
            print(f"  ❌ Qualifier Failed! Error: {res_qualifier.error_message}")

        # --- STAGE 4: Sales Copywriting & Lead Guidance (Complexity: COMPLEX) ---
        print("\n[Stage 4] Executing Sales Copywriting & Lead Guidance...")
        
        simulated_campaign = "Leeds Route Optimization Campaign"
        simulated_product = "Route planning and fleet optimization software for delivery companies."
        simulated_contact = "Liam O'Connor"
        simulated_title = "Fleet Operations Director"
        simulated_tone = "professional yet direct"
        simulated_objective = "Schedule a 15-minute product demo"
        simulated_evidence = (
            "Leeds Rapid Deliveries Ltd. operates an active fleet of 18 delivery drivers "
            "handling daily dispatch and last-mile parcel deliveries. They currently use "
            "manual methods for daily route planning, facing fuel inefficiencies."
        )

        print(f"👉 Generating pitch copy for: '{simulated_contact}' ({simulated_title}) at '{simulated_company}'")
        
        req_guidance = LLMRequest(
            operation=LLMOperation.GENERATE,
            complexity=LLMComplexity.COMPLEX,
            prompt_key="prospecting.lead_guidance.user",
            system_prompt_key="prospecting.lead_guidance.system",
            variables={
                "company_name": simulated_company,
                "campaign_name": simulated_campaign,
                "product_description": simulated_product,
                "contact_name": simulated_contact,
                "contact_title": simulated_title,
                "tone": simulated_tone,
                "objective": simulated_objective,
                "evidence": simulated_evidence
            },
            metadata={"domain": "prospecting", "test_run": "true", "stage": "guidance"}
        )
        res_guidance = router.execute(req_guidance)
        
        if res_guidance.is_success():
            print(f"\n  ✅ Guidance Success! (Model: {res_guidance.model} | Provider: {res_guidance.provider} | Attempts: {res_guidance.attempts})")
            print(f"  Latency: {res_guidance.latency_ms}ms | Tokens: {res_guidance.usage.get('total_tokens', 0)}")
            try:
                parsed_guidance = json.loads(res_guidance.raw_text)
                print("Outreach Guidance JSON Output:")
                print(json.dumps(parsed_guidance, indent=2))
            except json.JSONDecodeError:
                print(f"  Raw Text: {res_guidance.raw_text}")
        else:
            print(f"  ❌ Guidance Failed! Error: {res_guidance.error_message}")

        # --- STAGE 5: Email Reply Classifier (Complexity: STANDARD) ---
        print("\n[Stage 5] Executing Email Reply Classifier...")
        
        simulated_reply = "Please remove me from your list. I am not interested."
        print(f"👉 Analyzing inbound reply text: '{simulated_reply}'")
        
        req_classifier = LLMRequest(
            operation=LLMOperation.GENERATE,
            complexity=LLMComplexity.STANDARD,
            prompt_key="prospecting.reply_classifier.user",
            system_prompt_key="prospecting.reply_classifier.system",
            variables={
                "reply_text": simulated_reply
            },
            metadata={"domain": "prospecting", "test_run": "true", "stage": "reply_classifier"}
        )
        res_classifier = router.execute(req_classifier)
        
        if res_classifier.is_success():
            print(f"\n  ✅ Classifier Success! (Model: {res_classifier.model} | Provider: {res_classifier.provider} | Attempts: {res_classifier.attempts})")
            print(f"  Latency: {res_classifier.latency_ms}ms | Tokens: {res_classifier.usage.get('total_tokens', 0)}")
            try:
                parsed_classifier = json.loads(res_classifier.raw_text)
                print("Reply Classification JSON Output:")
                print(json.dumps(parsed_classifier, indent=2))
            except json.JSONDecodeError:
                print(f"  Raw Text: {res_classifier.raw_text}")
        else:
            print(f"  ❌ Classifier Failed! Error: {res_classifier.error_message}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test discovery pipeline LLM prompts repeatedly.")
    parser.add_argument("-n", "--iterations", type=int, default=1, help="Number of test iterations to run.")
    args, unknown = parser.parse_known_args()
    run_test(args.iterations)
