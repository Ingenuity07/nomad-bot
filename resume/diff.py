import difflib
from typing import Dict, Any, List

class SpecDiffEngine:
    """Compares two StructuredResumeSpec dicts and returns bullet-by-bullet and field-by-field Git-style diffs."""

    @staticmethod
    def diff_specs(old_spec: dict, new_spec: dict) -> Dict[str, Any]:
        """Compute the structural changes between two resume versions."""
        if not old_spec:
            old_spec = {}
        if not new_spec:
            new_spec = {}
            
        diff_report = {
            "summary_diff": SpecDiffEngine._diff_strings(old_spec.get("summary", ""), new_spec.get("summary", "")),
            "experience_diffs": SpecDiffEngine._diff_experiences(old_spec.get("experiences", []), new_spec.get("experiences", [])),
            "project_diffs": SpecDiffEngine._diff_projects(old_spec.get("projects", []), new_spec.get("projects", []))
        }
        return diff_report

    @staticmethod
    def _diff_strings(old_str: str, new_str: str) -> List[Dict[str, str]]:
        """Compare strings and return inline differences."""
        diff = difflib.ndiff(old_str.splitlines(), new_str.splitlines())
        res = []
        for line in diff:
            if line.startswith("+ "):
                res.append({"status": "added", "value": line[2:]})
            elif line.startswith("- "):
                res.append({"status": "removed", "value": line[2:]})
            elif line.startswith("  "):
                res.append({"status": "unchanged", "value": line[2:]})
        return res

    @staticmethod
    def _diff_experiences(old_list: list, new_list: list) -> List[Dict[str, Any]]:
        """Diff two work experience lists."""
        old_map = {f"{e.get('company')}:{e.get('role')}": e for e in old_list if e.get('company') and e.get('role')}
        new_map = {f"{e.get('company')}:{e.get('role')}": e for e in new_list if e.get('company') and e.get('role')}
        
        all_keys = set(old_map.keys()).union(new_map.keys())
        diffs = []
        for k in all_keys:
            old_val = old_map.get(k)
            new_val = new_map.get(k)
            
            if not old_val:
                diffs.append({
                    "status": "added", 
                    "company": new_val.get("company"), 
                    "role": new_val.get("role"), 
                    "bullets": [{"status": "added", "value": b} for b in new_val.get("bullet_points", [])]
                })
            elif not new_val:
                diffs.append({
                    "status": "removed", 
                    "company": old_val.get("company"), 
                    "role": old_val.get("role"), 
                    "bullets": [{"status": "removed", "value": b} for b in old_val.get("bullet_points", [])]
                })
            else:
                bullets_diff = SpecDiffEngine._diff_lists(old_val.get("bullet_points", []), new_val.get("bullet_points", []))
                diffs.append({
                    "status": "modified",
                    "company": new_val.get("company"),
                    "role": new_val.get("role"),
                    "bullets": bullets_diff
                })
        return diffs

    @staticmethod
    def _diff_projects(old_list: list, new_list: list) -> List[Dict[str, Any]]:
        """Diff two project lists."""
        old_map = {p.get("title"): p for p in old_list if p.get('title')}
        new_map = {p.get("title"): p for p in new_list if p.get('title')}
        
        all_keys = set(old_map.keys()).union(new_map.keys())
        diffs = []
        for k in all_keys:
            old_val = old_map.get(k)
            new_val = new_map.get(k)
            
            if not old_val:
                diffs.append({
                    "status": "added", 
                    "title": k, 
                    "bullets": [{"status": "added", "value": b} for b in new_val.get("bullet_points", [])]
                })
            elif not new_val:
                diffs.append({
                    "status": "removed", 
                    "title": k, 
                    "bullets": [{"status": "removed", "value": b} for b in old_val.get("bullet_points", [])]
                })
            else:
                bullets_diff = SpecDiffEngine._diff_lists(old_val.get("bullet_points", []), new_val.get("bullet_points", []))
                diffs.append({
                    "status": "modified",
                    "title": k,
                    "bullets": bullets_diff
                })
        return diffs

    @staticmethod
    def _diff_lists(old_items: List[str], new_items: List[str]) -> List[Dict[str, str]]:
        """Diff list strings (bullets/skills)."""
        diff = difflib.ndiff(old_items, new_items)
        res = []
        for item in diff:
            if item.startswith("+ "):
                res.append({"status": "added", "value": item[2:]})
            elif item.startswith("- "):
                res.append({"status": "removed", "value": item[2:]})
            elif item.startswith("  "):
                res.append({"status": "unchanged", "value": item[2:]})
        return res
